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
        # [MOD] Recuperiamo i nodi con metadata_json
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
        
        # Recupero Bulk dati
        contents_map = self.storage.get_contents_bulk(list(needed_hashes))
        files_map = self.storage.get_files_bulk(list(needed_files), repo_id=repo_id)
        definitions_map = self.storage.get_incoming_definitions_bulk(node_ids)
        
        texts_to_embed = []
        vector_docs = []
        
        for node in nodes:
            content = contents_map.get(node.get('chunk_hash'), "")
            file_info = files_map.get(node.get('file_path'), {})
            defined_symbols = definitions_map.get(node['id'], [])
            
            # --- CONTEXT ENRICHMENT ---
            
            # 1. Parsing Metadati Semantici (DB restituisce stringa JSON)
            meta_json = node.get('metadata_json')
            meta = {}
            if meta_json:
                try:
                    meta = json.loads(meta_json)
                except Exception:
                    pass
            
            context_parts = [
                "[CONTEXT]",
                f"File: {node.get('file_path')}",
                f"Language: {file_info.get('language')}",
            ]
            
            # 2. Aggiunta Tag Semantici al Vettore
            matches = meta.get('semantic_matches', [])
            
            # Raggruppiamo i tag per categoria per pulizia
            # Es. "Role: Entry Point, API Handler"
            roles = []
            others = []
            
            for m in matches:
                # Usiamo label (più descrittivo) se c'è, altrimenti value
                text = m.get('label') or m.get('value', '').replace('_', ' ')
                cat = m.get('category')
                
                if cat == 'role':
                    roles.append(text)
                elif cat == 'type':
                    pass # 'type' come 'function' è spesso ridondante col codice, lo ignoriamo se vogliamo risparmiare token
                else:
                    others.append(text)
            
            if roles:
                context_parts.append(f"Role: {', '.join(roles)}")
            if others:
                context_parts.append(f"Tags: {', '.join(others)}")

            # 3. Aggiunta Simboli Definiti (Calls)
            if defined_symbols:
                symbols_str = ", ".join(sorted(set(defined_symbols)))
                context_parts.append(f"Defines: {symbols_str}")
            
            context_parts.append(f"\n[CODE]\n{content}")
            
            full_text = "\n".join(context_parts)
            v_hash = hashlib.sha256(full_text.encode('utf-8')).hexdigest()
            
            texts_to_embed.append(full_text)
            
            # Documento DB (Senza Text Content duplicato)
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

        if texts_to_embed:
            vectors = self.provider.embed(texts_to_embed)
            for doc, vec in zip(vector_docs, vectors):
                doc["vector"] = vec
            self.storage.save_embeddings(vector_docs)
            
        return vector_docs