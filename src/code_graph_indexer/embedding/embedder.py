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

    def run_indexing(self, batch_size: int = 32, yield_debug_docs: bool = False) -> Generator[Dict[str, Any], None, None]:
        """
        Esegue l'embedding.
        
        Args:
            batch_size: Dimensione del batch per l'API.
            yield_debug_docs: Se True, yielda anche i documenti generati (PER DEBUG/TEST).
                              Se False (Prod), yielda solo lo stato di avanzamento.
        """
        # 1. Cursore leggero (Query 1)
        nodes_cursor = self.storage.get_nodes_cursor()
        
        current_batch = []
        processed_count = 0
        
        for node in nodes_cursor:
            current_batch.append(node)
            
            if len(current_batch) >= batch_size:
                # Processa e salva (Passiamo il flag debug)
                saved_docs = self._process_and_save(current_batch, debug=yield_debug_docs)
                
                # Aggiorna contatori
                processed_count += len(current_batch)
                
                # Yield Status (Sempre)
                yield {"status": "processing", "processed": processed_count}
                
                # Yield Docs (Solo se richiesto per debug)
                if yield_debug_docs:
                    for doc in saved_docs: yield doc
                
                current_batch = []
        
        # Flush finale
        if current_batch:
            saved_docs = self._process_and_save(current_batch, debug=yield_debug_docs)
            processed_count += len(current_batch)
            
            yield {"status": "completed", "processed": processed_count}
            
            if yield_debug_docs:
                for doc in saved_docs: yield doc

    def _process_and_save(self, nodes: List[Dict], debug: bool = False) -> List[Dict]:
        """
        Orchestra Arricchimento -> Embedding -> Salvataggio.
        """
        # 1. Raccogli chiavi per Fetch Batch
        needed_hashes = set()
        needed_files = set()
        node_ids = []

        for node in nodes:
            node_ids.append(node['id'])
            if node.get('chunk_hash'): needed_hashes.add(node['chunk_hash'])
            if node.get('file_path'): needed_files.add(node['file_path'])
        
        # 2. Fetch Batch
        contents_map = self.storage.get_contents_bulk(list(needed_hashes))
        files_map = self.storage.get_files_bulk(list(needed_files))
        definitions_map = self.storage.get_incoming_definitions_bulk(node_ids)
        
        texts_to_embed = []
        vector_docs = []
        
        for node in nodes:
            content = contents_map.get(node.get('chunk_hash'), "")
            file_info = files_map.get(node.get('file_path'), {})
            defined_symbols = definitions_map.get(node['id'], [])
            
            # --- COSTRUZIONE PAYLOAD SEMANTICO ---
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
                "repo_id": file_info.get('repo_id'),
                "file_path": node.get('file_path'),
                "directory": os.path.dirname(node.get('file_path', '')),
                "language": file_info.get('language'),
                "category": file_info.get('category'),
                "branch": "main",
                "chunk_type": node.get('type'),
                "start_line": node.get('start_line'),
                "end_line": node.get('end_line'),
                "text_content": content,
                "vector_hash": v_hash,
                "model_name": self.provider.model_name,
                "created_at": datetime.datetime.utcnow().isoformat()
            }
            
            # [MODIFICA] Aggiungi contesto solo se in debug
            if debug:
                doc["_debug_context"] = full_text
                
            vector_docs.append(doc)

        # 3. Chiamata API e Salvataggio
        if texts_to_embed:
            vectors = self.provider.embed(texts_to_embed)
            for doc, vec in zip(vector_docs, vectors):
                doc["vector"] = vec
            
            self.storage.save_embeddings(vector_docs)
            
        return vector_docs