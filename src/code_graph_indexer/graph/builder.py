import logging
import json
from typing import List, Dict, Any, Optional
from .base import CodeRelation
from ..models import ChunkNode, ChunkContent
from ..storage.base import GraphStorage

logger = logging.getLogger(__name__)

class KnowledgeGraphBuilder:
    def __init__(self, storage: GraphStorage):
        self.storage = storage

    def add_files(self, files: List):
        self.storage.add_files(files)

    def add_chunks(self, chunks: List):
        self.storage.add_nodes(chunks)
        
    def add_contents(self, contents: List):
        self.storage.add_contents(contents)

    def index_search_content(self, nodes: List[ChunkNode], contents: Dict[str, ChunkContent]):
        """
        Prepara e salva i dati per la ricerca FTS unificata (nodes_fts).
        Combina Path, Tag Semantici (Puliti e Deduplicati) e Contenuto.
        """
        search_batch = []
        
        for node in nodes:
            # 1. Recupera contenuto
            content_obj = contents.get(node.chunk_hash)
            raw_content = content_obj.content if content_obj else ""
            
            # 2. Recupera metadati
            meta = node.metadata or {}
            matches = meta.get("semantic_matches", [])
            
            # 3. Costruzione Smart dei Tag
            unique_tokens = set()
            
            for m in matches:
                # A. Valore Tecnico (es. "entry_point", "api_endpoint")
                # Lo teniamo intatto perché è un identificatore forte
                if 'value' in m:
                    unique_tokens.add(m['value'].lower())
                
                # B. Label Umana (es. "Application Entry Point")
                # La spezziamo in parole singole per permettere match parziali
                if 'label' in m:
                    # Rimuoviamo caratteri non alfanumerici base se necessario, 
                    # ma lo split spazio è solitamente sufficiente
                    words = m['label'].lower().split()
                    unique_tokens.update(words)
                
                # C. Categoria: LA IGNORIAMO. 
                # "role", "type" sono parole stop/inutili per la ricerca.
            
            # 4. Creiamo la stringa pulita
            # Es: "application entry point api_endpoint" (senza duplicati)
            tags_str = " ".join(sorted(unique_tokens))
            
            search_batch.append({
                "node_id": node.id,
                "file_path": node.file_path,
                "tags": tags_str,
                "content": raw_content
            })
            
        if hasattr(self.storage, 'add_search_index'):
            self.storage.add_search_index(search_batch)
    def add_relations(self, relations: List[CodeRelation], repo_id: str = None):
        """
        Aggiunge le relazioni al grafo.
        """
        # (Logica invariata rispetto alle versioni precedenti)
        # Ottimizzazione lookup cache per evitare query ripetute su find_chunk_id
        logger.info(f"Elaborazione di {len(relations)} relazioni...")
        lookup_cache = {}
        
        if hasattr(self.storage, 'commit'): self.storage.commit()

        for rel in relations:
            # 1. Source Resolution
            source_id = rel.source_id
            if not source_id:
                if not rel.source_byte_range or len(rel.source_byte_range) != 2: continue
                src_key = (rel.source_file, tuple(rel.source_byte_range))
                source_id = lookup_cache.get(src_key)
                if not source_id:
                    source_id = self.storage.find_chunk_id(rel.source_file, rel.source_byte_range, repo_id=repo_id)
                    if source_id: lookup_cache[src_key] = source_id
            
            if not source_id: continue 

            # 2. Target Resolution
            target_id = rel.target_id
            if not target_id:
                if rel.metadata.get("is_external"):
                    target_id = rel.target_file
                    self.storage.ensure_external_node(target_id)
                else:
                    if not rel.target_byte_range or len(rel.target_byte_range) != 2: continue
                    tgt_key = (rel.target_file, tuple(rel.target_byte_range))
                    target_id = lookup_cache.get(tgt_key)
                    if not target_id:
                        target_id = self.storage.find_chunk_id(rel.target_file, rel.target_byte_range, repo_id=repo_id)
                        if target_id: lookup_cache[tgt_key] = target_id

            if target_id and source_id != target_id:
                self.storage.add_edge(source_id, target_id, rel.relation_type, rel.metadata)

            if len(lookup_cache) > 20000: lookup_cache.clear()
        
        if hasattr(self.storage, 'commit'): self.storage.commit()


    def get_stats(self): return self.storage.get_stats()