import uuid
import hashlib
import datetime
import logging
import os
import json
from typing import List, Dict, Any, Generator
from ..storage.base import GraphStorage
from ..providers.embedding import EmbeddingProvider

logger = logging.getLogger(__name__)

class CodeEmbedder:
    def __init__(self, storage: GraphStorage, provider: EmbeddingProvider):
        self.storage = storage
        self.provider = provider

    def run_indexing(self, repo_id: str, branch: str, batch_size: int = 32, yield_debug_docs: bool = False) -> Generator[Dict[str, Any], None, None]:
        nodes_cursor = self.storage.get_nodes_to_embed(
            repo_id=repo_id, 
            model_name=self.provider.model_name
        )
        
        current_batch = []
        processed_count = 0
        
        for node in nodes_cursor:
            current_batch.append(node)
            
            if len(current_batch) >= batch_size:
                saved_docs = self._process_and_save(current_batch, repo_id, branch, debug=yield_debug_docs)
                processed_count += len(current_batch)
                yield {"status": "processing", "processed": processed_count}
                if yield_debug_docs:
                    for doc in saved_docs: yield doc
                current_batch = []
        
        if current_batch:
            saved_docs = self._process_and_save(current_batch, repo_id, branch, debug=yield_debug_docs)
            processed_count += len(current_batch)
            yield {"status": "completed", "processed": processed_count}
            if yield_debug_docs:
                for doc in saved_docs: yield doc
                
        if processed_count == 0:
            yield {"status": "skipped", "message": "All nodes already embedded"}

    def _process_and_save(self, nodes: List[Dict], repo_id: str, branch: str, debug: bool = False) -> List[Dict]:
        needed_hashes = set()
        needed_files = set()
        node_ids = []

        for node in nodes:
            node_ids.append(node['id'])
            if node.get('chunk_hash'): needed_hashes.add(node['chunk_hash'])
            if node.get('file_path'): needed_files.add(node['file_path'])
        
        # 1. Recupero dati base
        contents_map = self.storage.get_contents_bulk(list(needed_hashes))
        files_map = self.storage.get_files_bulk(list(needed_files), repo_id=repo_id)
        definitions_map = self.storage.get_incoming_definitions_bulk(node_ids)
        
        vector_docs = []
        hash_to_text = {} 
        
        # 2. Costruzione Prompt e Calcolo Hash
        for node in nodes:
            content = contents_map.get(node.get('chunk_hash'), "")
            file_info = files_map.get(node.get('file_path'), {})
            defined_symbols = definitions_map.get(node['id'], [])
            
            # Parsing Metadati
            meta_json = node.get('metadata_json')
            meta = {}
            if meta_json:
                try: meta = json.loads(meta_json)
                except: pass
            
            context_parts = [
                "[CONTEXT]",
                f"File: {node.get('file_path')}",
                f"Language: {file_info.get('language')}",
            ]
            
            matches = meta.get('semantic_matches', [])
            roles = [m.get('label') or m.get('value', '').replace('_', ' ') for m in matches if m.get('category') == 'role']
            others = [m.get('label') or m.get('value', '').replace('_', ' ') for m in matches if m.get('category') not in ('role', 'type')]
            
            if roles: context_parts.append(f"Role: {', '.join(roles)}")
            if others: context_parts.append(f"Tags: {', '.join(others)}")
            if defined_symbols:
                symbols_str = ", ".join(sorted(set(defined_symbols)))
                context_parts.append(f"Defines: {symbols_str}")
            
            context_parts.append(f"\n[CODE]\n{content}")
            full_text = "\n".join(context_parts)
            
            v_hash = hashlib.sha256(full_text.encode('utf-8')).hexdigest()
            if v_hash not in hash_to_text:
                hash_to_text[v_hash] = full_text

            doc = {
                "id": str(uuid.uuid4()),
                "chunk_id": node['id'],
                "repo_id": repo_id,
                "branch": branch,
                "file_path": node.get('file_path'),
                "directory": os.path.dirname(node.get('file_path', '')),
                "language": file_info.get('language'),
                "category": file_info.get('category'),
                "start_line": node.get('start_line'),
                "end_line": node.get('end_line'),
                "vector_hash": v_hash,
                "model_name": self.provider.model_name,
                "created_at": datetime.datetime.utcnow().isoformat()
            }
            if debug: doc["_debug_context"] = full_text
            vector_docs.append(doc)

        # 3. Check Cache
        unique_hashes = list(hash_to_text.keys())
        cached_vectors = {}
        if hasattr(self.storage, 'get_vectors_by_hashes'):
            cached_vectors = self.storage.get_vectors_by_hashes(unique_hashes, self.provider.model_name)
        
        missing_hashes = [h for h in unique_hashes if h not in cached_vectors]
        
        # 4. Calcolo Nuovi
        if missing_hashes:
            missing_texts = [hash_to_text[h] for h in missing_hashes]
            logger.info(f"🧠 Computing {len(missing_texts)} new vectors (Cached: {len(cached_vectors)})")
            
            new_embeddings = self.provider.embed(missing_texts)
            
            for h, vec in zip(missing_hashes, new_embeddings):
                cached_vectors[h] = vec
        else:
            if cached_vectors:
                logger.info(f"⚡ All {len(unique_hashes)} vectors retrieved from cache")

        # 5. Costruzione Lista Finale
        final_docs_to_save = []
        
        for doc in vector_docs:
            v_h = doc["vector_hash"]
            if v_h in cached_vectors:
                # [FIX CRITICO] La chiave deve essere 'embedding' per matchare %(embedding)s
                doc["embedding"] = cached_vectors[v_h]
                final_docs_to_save.append(doc)
            else:
                logger.error(f"Vector missing for hash {v_h}")
        
        # 6. Salvataggio
        if final_docs_to_save:
            self.storage.save_embeddings(final_docs_to_save)
            
        return final_docs_to_save