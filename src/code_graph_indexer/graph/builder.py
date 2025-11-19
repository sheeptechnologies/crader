import logging
from typing import List
from .base import CodeRelation
from ..storage.base import GraphStorage

logger = logging.getLogger(__name__)

class KnowledgeGraphBuilder:
    """
    Orchestrator per la costruzione del grafo.
    Delega la persistenza al GraphStorage iniettato.
    """

    def __init__(self, storage: GraphStorage):
        self.storage = storage

    def add_files(self, files: List):
        """Salva i metadati dei file."""
        self.storage.add_files(files)

    def add_chunks(self, chunks: List):
        """Salva i nodi strutturali."""
        self.storage.add_nodes(chunks)
        
    def add_contents(self, contents: List):
        """Salva i contenuti testuali."""
        self.storage.add_contents(contents)

    def add_relations(self, relations: List[CodeRelation]):
        logger.info(f"Elaborazione di {len(relations)} relazioni...")
        lookup_cache = {}
        
        if hasattr(self.storage, 'commit'): self.storage.commit()

        for rel in relations:
            # Validazione
            if not rel.source_byte_range or len(rel.source_byte_range) != 2: continue
            if not rel.metadata.get("is_external") and (not rel.target_byte_range or len(rel.target_byte_range) != 2): continue

            # Source Lookup
            src_key = (rel.source_file, tuple(rel.source_byte_range))
            source_id = lookup_cache.get(src_key)
            if not source_id:
                source_id = self.storage.find_chunk_id(rel.source_file, rel.source_byte_range)
                if source_id: lookup_cache[src_key] = source_id
            
            if not source_id: continue

            # Target Lookup
            target_id = None
            if rel.metadata.get("is_external"):
                target_id = rel.target_file
                self.storage.ensure_external_node(target_id)
            else:
                tgt_key = (rel.target_file, tuple(rel.target_byte_range))
                target_id = lookup_cache.get(tgt_key)
                if not target_id:
                    target_id = self.storage.find_chunk_id(rel.target_file, rel.target_byte_range)
                    if target_id: lookup_cache[tgt_key] = target_id

            # Edge Creation
            if target_id and source_id != target_id:
                self.storage.add_edge(source_id, target_id, rel.relation_type, rel.metadata)

            if len(lookup_cache) > 20000: lookup_cache.clear()
        
        if hasattr(self.storage, 'commit'): self.storage.commit()

    def get_stats(self): return self.storage.get_stats()
    
    # Metodi proxy per compatibilità (opzionali se usi direttamente lo storage)
    def export_json(self, p): 
        if hasattr(self.storage, 'export_json'): self.storage.export_json(p)