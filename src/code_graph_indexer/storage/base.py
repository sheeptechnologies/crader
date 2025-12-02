from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional, Generator

class GraphStorage(ABC):
    """
    Interfaccia astratta per lo storage (Staging Area).
    Supporta: Files, Nodes, Edges, Contents + Embeddings + Repositories + FTS.
    """
    
    # --- REPOSITORY MANAGEMENT ---
    @abstractmethod
    def get_repository(self, repo_id: str) -> Optional[Dict[str, Any]]: pass
    @abstractmethod
    def get_repository_by_context(self, url: str, branch: str) -> Optional[Dict[str, Any]]: pass
    @abstractmethod
    def register_repository(self, id: str, name: str, url: str, branch: str, commit_hash: str, local_path: str = None) -> str: pass
    @abstractmethod
    def update_repository_status(self, repo_id: str, status: str, commit_hash: str = None): pass

    # --- WRITE ---
    @abstractmethod
    def add_files(self, files: List[Any]): pass
    @abstractmethod
    def add_nodes(self, nodes: List[Any]): pass
    @abstractmethod
    def add_contents(self, contents: List[Any]): pass
    @abstractmethod
    def add_edge(self, source_id: str, target_id: str, relation_type: str, metadata: Dict[str, Any]): pass
    
    # [NEW] Metodo FTS Unificato
    @abstractmethod
    def add_search_index(self, search_docs: List[Dict[str, Any]]): 
        """Popola l'indice di ricerca testuale (Path + Tags + Content)."""
        pass

    @abstractmethod
    def delete_previous_data(self, repo_id: str, branch: str): pass

    # --- READ / LOOKUP ---
    @abstractmethod
    def find_chunk_id(self, file_path: str, byte_range: List[int], repo_id: str = None) -> Optional[str]: pass
    @abstractmethod
    def ensure_external_node(self, node_id: str): pass
    @abstractmethod
    def save_embeddings(self, vector_documents: List[Dict[str, Any]]): pass
    
    # --- BATCH ---
    @abstractmethod
    def get_nodes_cursor(self, repo_id: str = None, branch: str = None) -> Generator[Dict[str, Any], None, None]: pass
    @abstractmethod
    def get_nodes_to_embed(self, repo_id: str, model_name: str) -> Generator[Dict[str, Any], None, None]: pass
    @abstractmethod
    def get_contents_bulk(self, chunk_hashes: List[str]) -> Dict[str, str]: pass
    @abstractmethod
    def get_files_bulk(self, file_paths: List[str], repo_id: str = None) -> Dict[str, Dict[str, Any]]: pass
    @abstractmethod
    def get_incoming_definitions_bulk(self, node_ids: List[str]) -> Dict[str, List[str]]: pass
    
    # --- GENERAL STATS ---
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
    @abstractmethod
    def commit(self): pass
    @abstractmethod
    def close(self): pass

    # --- RETRIEVAL & NAVIGATION ---
    @abstractmethod
    def search_fts(self, query: str, limit: int = 20, repo_id: str = None, branch: str = None) -> List[Dict[str, Any]]: pass
    @abstractmethod
    def search_vectors(self, query_vector: List[float], limit: int = 20, repo_id: str = None, branch: str = None) -> List[Dict[str, Any]]: pass
    
    @abstractmethod
    def get_context_neighbors(self, node_id: str) -> Dict[str, List[Dict[str, Any]]]: pass
    @abstractmethod
    def get_neighbor_chunk(self, node_id: str, direction: str = "next") -> Optional[Dict[str, Any]]: pass
    @abstractmethod
    def get_incoming_references(self, target_node_id: str, limit: int = 50) -> List[Dict[str, Any]]: pass
    @abstractmethod
    def get_outgoing_calls(self, source_node_id: str, limit: int = 50) -> List[Dict[str, Any]]: pass