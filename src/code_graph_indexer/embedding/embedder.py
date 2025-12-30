import asyncio
import hashlib
import logging
import os
import json
import uuid
import datetime
import random
from typing import List, Dict, Any, AsyncGenerator, Tuple
from concurrent.futures import ProcessPoolExecutor

from ..storage.base import GraphStorage
from ..providers.embedding import EmbeddingProvider

logger = logging.getLogger(__name__)

# --- CPU BOUND TASKS ---

def _compute_prompt_and_hash(node: Dict[str, Any]) -> Tuple[str, str]:
    lang = node.get('language', 'text')
    category = node.get('category', 'unknown')
    content = node.get('content', '')
    definitions = node.get('incoming_definitions', [])
    
    meta_json = node.get('metadata_json')
    meta = {}
    if meta_json:
        try: meta = json.loads(meta_json)
        except: pass
    
    context_parts = [
        "[CONTEXT]",
        f"File: {node.get('file_path')}",
        f"Language: {lang}",
        f"Category: {category}"
    ]
    
    matches = meta.get('semantic_matches', [])
    roles = [m.get('label') or m.get('value', '').replace('_', ' ') for m in matches if m.get('category') == 'role']
    others = [m.get('label') or m.get('value', '').replace('_', ' ') for m in matches if m.get('category') not in ('role', 'type')]
    
    if roles: context_parts.append(f"Role: {', '.join(roles)}")
    if others: context_parts.append(f"Tags: {', '.join(others)}")
    
    if definitions:
        symbols_str = ", ".join(sorted(set(definitions)))
        context_parts.append(f"Defines: {symbols_str}")
    
    context_parts.append(f"\n[CODE]\n{content}")
    full_text = "\n".join(context_parts)
    
    v_hash = hashlib.sha256(full_text.encode('utf-8')).hexdigest()
    
    return full_text, v_hash

def _prepare_batch_for_staging(nodes: List[Dict], model_name: str, snapshot_id: str) -> List[Tuple]:
    """
    [FIX] Include snapshot_id nelle tuple raw per isolamento job.
    """
    prepared_rows = []
    for node in nodes:
        full_text, v_hash = _compute_prompt_and_hash(node)
        
        row = (
            str(uuid.uuid4()),      # id
            node['id'],             # chunk_id
            snapshot_id,            # snapshot_id (NEW)
            v_hash,                 # vector_hash
            node.get('file_path'),
            node.get('language'),
            node.get('category'),
            node.get('start_line'),
            node.get('end_line'),
            model_name,
            full_text               # content
        )
        prepared_rows.append(row)
    return prepared_rows


class CodeEmbedder:
    def __init__(self, storage: GraphStorage, provider: EmbeddingProvider):
        self.storage = storage
        self.provider = provider
        self.process_pool = ProcessPoolExecutor(max_workers=min(4, os.cpu_count() or 1))

    async def run_indexing(self, snapshot_id: str, batch_size: int = 1000, mock_api: bool = True) -> AsyncGenerator[Dict[str, Any], None]:
        logger.info(f"🚀 Starting Async Indexing for snapshot {snapshot_id} (Mock={mock_api})")
        
        try:
            # 1. SETUP STAGING (Global Unlogged Table)
            yield {"status": "init", "message": "Preparing staging environment..."}
            if hasattr(self.storage, 'prepare_embedding_staging'):
                self.storage.prepare_embedding_staging()
            
            # 2. PRODUCER PHASE
            yield {"status": "staging_start", "message": "Streaming enriched nodes from DB..."}
            
            nodes_iter = self.storage.get_nodes_to_embed(
                snapshot_id=snapshot_id, 
                model_name=self.provider.model_name,
                batch_size=batch_size
            )
            
            loop = asyncio.get_running_loop()
            current_batch = []
            total_staged = 0
            
            for node in nodes_iter:
                current_batch.append(node)
                if len(current_batch) >= batch_size:
                    await self._process_and_stage_batch(current_batch, snapshot_id, loop)
                    total_staged += len(current_batch)
                    yield {"status": "staging_progress", "staged": total_staged}
                    current_batch = []
            
            if current_batch:
                await self._process_and_stage_batch(current_batch, snapshot_id, loop)
                total_staged += len(current_batch)
            
            yield {"status": "staging_complete", "total_staged": total_staged}

            # 3. DEDUPLICATION PHASE
            yield {"status": "deduplicating", "message": "Backfilling from history..."}
            recovered_count = 0
            if hasattr(self.storage, 'backfill_staging_vectors'):
                recovered_count = self.storage.backfill_staging_vectors(snapshot_id)
            yield {"status": "deduplication_stats", "recovered": recovered_count}
            
            # 4. FLUSH HITS
            flushed_hits = 0
            if hasattr(self.storage, 'flush_staged_hits'):
                flushed_hits = self.storage.flush_staged_hits(snapshot_id)
            yield {"status": "flushed_hits", "count": flushed_hits}
            
            # 5. DELTA PHASE
            yield {"status": "embedding_start", "message": "Processing new vectors (Delta)..."}
            delta_processed = 0
            
            if hasattr(self.storage, 'fetch_staging_delta'):
                delta_gen = self.storage.fetch_staging_delta(snapshot_id, batch_size=500)
                
                for batch in delta_gen:
                    prompts = [item['content'] for item in batch]
                    
                    if mock_api:
                        vectors = await self._mock_embed_async(prompts)
                    else:
                        vectors = await loop.run_in_executor(None, self.provider.embed, prompts)

                    records_to_save = []
                    for item, vec in zip(batch, vectors):
                        records_to_save.append({
                            "id": item['id'],
                            "chunk_id": item['chunk_id'],
                            "snapshot_id": snapshot_id,
                            "vector_hash": item['vector_hash'],
                            "model_name": self.provider.model_name,
                            "created_at": datetime.datetime.utcnow(),
                            "file_path": item.get('file_path'),
                            "language": item.get('language'),
                            "category": item.get('category'),
                            "start_line": item.get('start_line'),
                            "end_line": item.get('end_line'),
                            "embedding": vec
                        })
                    
                    if hasattr(self.storage, 'save_embeddings_direct'):
                        self.storage.save_embeddings_direct(records_to_save)
                    else:
                        self.storage.save_embeddings(records_to_save)
                    
                    delta_processed += len(records_to_save)
                    yield {"status": "embedding_progress", "current_batch": len(records_to_save), "total_embedded": delta_processed}

            yield {
                "status": "completed", 
                "total_nodes": total_staged,
                "recovered_from_history": recovered_count,
                "newly_embedded": delta_processed
            }

        finally:
            # CLEANUP FINALE: Rimuove i dati di staging per questo snapshot
            if hasattr(self.storage, 'cleanup_staging'):
                self.storage.cleanup_staging(snapshot_id)

    async def _process_and_stage_batch(self, nodes: List[Dict], snapshot_id: str, loop):
        prepared_data = await loop.run_in_executor(
            self.process_pool, 
            _prepare_batch_for_staging, 
            nodes, 
            self.provider.model_name,
            snapshot_id 
        )
        if hasattr(self.storage, 'load_staging_data'):
            await loop.run_in_executor(None, self.storage.load_staging_data, iter(prepared_data))

    async def _mock_embed_async(self, texts: List[str]) -> List[List[float]]:
        latency = random.uniform(0.05, 0.2)
        await asyncio.sleep(latency)
        dim = self.provider.dimension
        return [[random.random() for _ in range(dim)] for _ in texts]