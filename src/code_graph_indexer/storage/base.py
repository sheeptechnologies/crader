from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional, Generator

class GraphStorage(ABC):
    """
    Interfaccia astratta per lo storage (Staging Area).
    Supporta: Files, Nodes, Edges, Contents + Embeddings & Search.
    """
    
    # --- WRITE (Graph) ---
    @abstractmethod
    def add_files(self, files: List[Any]): pass

    @abstractmethod
    def add_nodes(self, nodes: List[Any]): pass

    @abstractmethod
    def add_contents(self, contents: List[Any]): pass

    @abstractmethod
    def add_edge(self, source_id: str, target_id: str, relation_type: str, metadata: Dict[str, Any]): pass

    # --- LOOKUP (Graph) ---
    @abstractmethod
    def find_chunk_id(self, file_path: str, byte_range: List[int]) -> Optional[str]: pass

    @abstractmethod
    def ensure_external_node(self, node_id: str): pass
    
    # --- WRITE (Embeddings) ---
    @abstractmethod
    def save_embeddings(self, vector_documents: List[Dict[str, Any]]):
        """Salva i documenti vettoriali denormalizzati."""
        pass

    # --- READ (Batch & Optimization for Embedder) ---
    @abstractmethod
    def get_nodes_cursor(self) -> Generator[Dict[str, Any], None, None]:
        """Stream leggero dei nodi candidati per l'embedding."""
        pass

    @abstractmethod
    def get_contents_bulk(self, chunk_hashes: List[str]) -> Dict[str, str]:
        """Recupero massivo dei contenuti."""
        pass

    @abstractmethod
    def get_files_bulk(self, file_paths: List[str]) -> Dict[str, Dict[str, Any]]:
        """Recupero massivo metadati file."""
        pass

    @abstractmethod
    def get_incoming_definitions_bulk(self, node_ids: List[str]) -> Dict[str, List[str]]:
        """Recupera simboli definiti (via archi 'calls' entranti) per il batch di nodi."""
        pass

    # --- READ / CONSUME (Graph) ---
    @abstractmethod
    def get_all_files(self) -> Generator[Dict[str, Any], None, None]: pass

    @abstractmethod
    def get_all_nodes(self) -> Generator[Dict[str, Any], None, None]: pass

    @abstractmethod
    def get_all_contents(self) -> Generator[Dict[str, Any], None, None]: pass

    @abstractmethod
    def get_all_edges(self) -> Generator[Dict[str, Any], None, None]: pass

    @abstractmethod
    def get_stats(self) -> Dict[str, int]: pass

    # --- LIFECYCLE ---
    @abstractmethod
    def commit(self): pass
    
    @abstractmethod
    def close(self): pass