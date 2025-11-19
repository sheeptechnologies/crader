from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional, Generator

class GraphStorage(ABC):
    """
    Interfaccia astratta per lo storage (Staging Area).
    Supporta 4 tabelle: Files, Nodes, Edges, Contents.
    """
    
    # --- WRITE ---
    @abstractmethod
    def add_files(self, files: List[Any]):
        """Salva i metadati dei file."""
        pass

    @abstractmethod
    def add_nodes(self, nodes: List[Any]):
        """Salva i nodi strutturali."""
        pass

    @abstractmethod
    def add_contents(self, contents: List[Any]):
        """Salva i contenuti unici."""
        pass

    @abstractmethod
    def add_edge(self, source_id: str, target_id: str, relation_type: str, metadata: Dict[str, Any]):
        pass

    # --- LOOKUP ---
    @abstractmethod
    def find_chunk_id(self, file_path: str, byte_range: List[int]) -> Optional[str]:
        pass

    @abstractmethod
    def ensure_external_node(self, node_id: str):
        pass
    
    # --- READ / CONSUME ---
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