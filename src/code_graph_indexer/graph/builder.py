import logging
import json
from typing import List, Dict, Any, Optional
from .base import CodeRelation
from ..models import ChunkNode, ChunkContent
from ..storage.base import GraphStorage

logger = logging.getLogger(__name__)

class KnowledgeGraphBuilder:
    """
    Costruisce il grafo scrivendo nodi, file e relazioni sullo Storage.
    Snapshot-Aware: Le relazioni esterne (SCIP) vengono risolte nel contesto dello snapshot corrente.
    """
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
        Prepara e salva i dati per la ricerca FTS unificata.
        """
        search_batch = []
        for node in nodes:
            content_obj = contents.get(node.chunk_hash)
            raw_content = content_obj.content if content_obj else ""
            
            meta = node.metadata or {}
            matches = meta.get("semantic_matches", [])
            
            unique_tokens = set()
            for m in matches:
                if 'value' in m: unique_tokens.add(m['value'].lower())
                if 'label' in m: unique_tokens.update(m['label'].lower().split())
            
            tags_str = " ".join(sorted(unique_tokens))
            
            search_batch.append({
                "node_id": node.id,
                "file_path": node.file_path,
                "tags": tags_str,
                "content": raw_content
            })
            
        if hasattr(self.storage, 'add_search_index'):
            self.storage.add_search_index(search_batch)

    def add_relations(self, relations: List[CodeRelation], snapshot_id: str = None):
        """
        Aggiunge le relazioni al grafo.
        
        Args:
            relations: Lista di oggetti CodeRelation.
            snapshot_id: FONDAMENTALE per risolvere le relazioni SCIP (byte-range -> node_id).
                         Se le relazioni hanno già source_id/target_id (interne), questo parametro è ignorato.
        """
        if not relations: return
        
        logger.info(f"Elaborazione di {len(relations)} relazioni (Context Snap: {snapshot_id})...")
        lookup_cache = {}
        
        # Helper per risolvere ID da range
        def resolve_id(file_path, byte_range):
            if not snapshot_id: return None
            key = (file_path, tuple(byte_range))
            if key in lookup_cache: return lookup_cache[key]
            
            # Chiamata allo storage con snapshot_id
            nid = self.storage.find_chunk_id(file_path, byte_range, snapshot_id)
            if nid: lookup_cache[key] = nid
            return nid

        for rel in relations:
            # 1. Source Resolution
            if not rel.source_id:
                if rel.source_byte_range and len(rel.source_byte_range) == 2:
                    rel.source_id = resolve_id(rel.source_file, rel.source_byte_range)
            
            if not rel.source_id: continue 

            # 2. Target Resolution
            if not rel.target_id:
                if rel.metadata.get("is_external"):
                    # Gestione nodi esterni (es. librerie std) - Placeholder
                    # self.storage.ensure_external_node(rel.target_file) 
                    rel.target_id = rel.target_file # Semplificazione per nodi fantasma
                elif rel.target_byte_range and len(rel.target_byte_range) == 2:
                    rel.target_id = resolve_id(rel.target_file, rel.target_byte_range)

            # 3. Scrittura Edge
            if rel.target_id and rel.source_id != rel.target_id:
                self.storage.add_edge(rel.source_id, rel.target_id, rel.relation_type, rel.metadata)

            if len(lookup_cache) > 20000: lookup_cache.clear()
        
    def get_stats(self): return self.storage.get_stats()