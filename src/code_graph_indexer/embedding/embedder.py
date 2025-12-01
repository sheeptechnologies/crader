import uuid
import hashlib
import datetime
import logging
import os
from typing import List, Dict, Any, Generator
from ..storage.base import GraphStorage
from ..providers.embedding import EmbeddingProvider

logger = logging.getLogger(__name__)

class CodeEmbedder:
    def __init__(self, storage: GraphStorage, provider: EmbeddingProvider):
        self.storage = storage
        self.provider = provider

    def run_indexing(self, repo_id: str, branch: str, batch_size: int = 32, yield_debug_docs: bool = False) -> Generator[Dict[str, Any], None, None]:
        """
        Esegue l'embedding filtrando per repo_id e branch.
        
        Args:
            repo_id: ID della repository target.
            branch: Branch corrente per filtrare e taggare i vettori. (obsoleto, repo_id è sufficiente)
            batch_size: Dimensione del batch per l'API.
            yield_debug_docs: Se True, yielda anche i documenti generati (PER DEBUG/TEST).
                              Se False (Prod), yielda solo lo stato di avanzamento.
        """

        # [OTTIMIZZAZIONE] Chiediamo al DB solo quello che serve
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
        
        # Flush finale
        if current_batch:
            saved_docs = self._process_and_save(current_batch, repo_id, branch, debug=yield_debug_docs)
            processed_count += len(current_batch)
            yield {"status": "completed", "processed": processed_count}
            if yield_debug_docs:
                for doc in saved_docs: yield doc
        
        # Se non c'era nulla da fare
        if processed_count == 0:
            yield {"status": "skipped", "message": "All nodes already embedded"}

    def _process_and_save(self, nodes: List[Dict], repo_id: str, branch: str, debug: bool = False) -> List[Dict]:
        """
        Orchestra Arricchimento -> Embedding -> Salvataggio.
        """
        needed_hashes = set()
        needed_files = set()
        node_ids = []

        for node in nodes:
            node_ids.append(node['id'])
            if node.get('chunk_hash'): needed_hashes.add(node['chunk_hash'])
            if node.get('file_path'): needed_files.add(node['file_path'])
        
        contents_map = self.storage.get_contents_bulk(list(needed_hashes))
        files_map = self.storage.get_files_bulk(list(needed_files))
        definitions_map = self.storage.get_incoming_definitions_bulk(node_ids)
        
        texts_to_embed = []
        vector_docs = []
        
        for node in nodes:
            content = contents_map.get(node.get('chunk_hash'), "")
            file_info = files_map.get(node.get('file_path'), {})
            defined_symbols = definitions_map.get(node['id'], [])
            
            # Context construction
            context_parts = [
                "[CONTEXT]",
                f"File: {node.get('file_path')}",
                f"Type: {node.get('type')}",
                f"Category: {file_info.get('category', 'code')}"
            ]
            
            if defined_symbols:
                symbols_str = ", ".join(sorted(set(defined_symbols)))
                context_parts.append(f"\n[DEFINITIONS]\nDefines: {symbols_str}")
            
            context_parts.append(f"\n[CODE]\n{content}")
            
            full_text = "\n".join(context_parts)
            v_hash = hashlib.sha256(full_text.encode('utf-8')).hexdigest()
            
            texts_to_embed.append(full_text)
            
            doc = {
                "id": str(uuid.uuid4()),
                "chunk_id": node['id'],
                "repo_id": repo_id,       # [FIX] Usiamo il repo_id passato
                "branch": branch,         # [FIX] Usiamo il branch passato
                "file_path": node.get('file_path'),
                "directory": os.path.dirname(node.get('file_path', '')),
                "language": file_info.get('language'),
                "category": file_info.get('category'),
                "chunk_type": node.get('type'),
                "start_line": node.get('start_line'),
                "end_line": node.get('end_line'),
                "text_content": content,
                "vector_hash": v_hash,
                "model_name": self.provider.model_name,
                "created_at": datetime.datetime.utcnow().isoformat()
            }
            
            if debug:
                doc["_debug_context"] = full_text
                
            vector_docs.append(doc)

        if texts_to_embed:
            vectors = self.provider.embed(texts_to_embed)
            for doc, vec in zip(vector_docs, vectors):
                doc["vector"] = vec
            
            self.storage.save_embeddings(vector_docs)
            
        return vector_docs