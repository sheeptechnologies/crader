from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional, Generator, Tuple

class GraphStorage(ABC):
    """
    Interfaccia astratta per lo storage (Staging Area).
    Supporta: Files, Nodes, Edges, Contents + Embeddings + Repositories.
    """
    
    # --- REPOSITORY MANAGEMENT (FASE 1) ---
    @abstractmethod
    def get_repository(self, repo_id: str) -> Optional[Dict[str, Any]]:
        """Recupera lo stato di una repository dato il suo ID univoco."""
        pass

    @abstractmethod
    def register_repository(self, repo_id: str, name: str, url: str, branch: str, commit_hash: str):
        """Registra o aggiorna l'inizio di un'indicizzazione."""
        pass

    @abstractmethod
    def update_repository_status(self, repo_id: str, status: str, commit_hash: str = None):
        """Aggiorna lo stato (es. 'completed', 'failed') e il commit finale."""
        pass


    # --- WRITE (Graph) ---
    @abstractmethod
    def add_files(self, files: List[Any]): pass

    @abstractmethod
    def add_nodes(self, nodes: List[Any]): pass

    @abstractmethod
    def add_contents(self, contents: List[Any]): pass

    @abstractmethod
    def add_edge(self, source_id: str, target_id: str, relation_type: str, metadata: Dict[str, Any]): pass

    # --- DELETE (Graph) ---
    @abstractmethod
    def delete_previous_data(self, repo_id: str, branch: str):
        """
        Cancella i dati precedenti (es. embeddings) per un dato repo e branch
        prima di una nuova indicizzazione, per evitare duplicati.
        """
        pass

    # --- LOOKUP (Graph) ---
    @abstractmethod
    def find_chunk_id(self, file_path: str, byte_range: List[int]) -> Optional[str]: pass

    @abstractmethod
    def ensure_external_node(self, node_id: str): pass
    
    # --- WRITE (Embeddings) ---
    @abstractmethod
    def save_embeddings(self, vector_documents: List[Dict[str, Any]]): pass

    # --- READ (Batch & Optimization) ---
    @abstractmethod
    def get_nodes_cursor(self, repo_id: str = None, branch: str = None) -> Generator[Dict[str, Any], None, None]: pass

    @abstractmethod
    def get_contents_bulk(self, chunk_hashes: List[str]) -> Dict[str, str]: pass

    @abstractmethod
    def get_files_bulk(self, file_paths: List[str]) -> Dict[str, Dict[str, Any]]: pass

    @abstractmethod
    def get_incoming_definitions_bulk(self, node_ids: List[str]) -> Dict[str, List[str]]: pass

    @abstractmethod
    def get_nodes_to_embed(self, repo_id: str, model_name: str) -> Generator[Dict[str, Any], None, None]:
        """
        Restituisce SOLO i nodi che non hanno ancora un embedding per il modello specificato.
        Ottimizzato via SQL per evitare trasferimenti dati inutili.
        """
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

    # --- RETRIEVAL ---

    @abstractmethod
    def search_fts(self, query: str, limit: int = 20, repo_id: str = None, branch: str = None) -> List[Dict[str, Any]]:
        """
        Esegue una ricerca Full-Text (BM25/Trigram) sui contenuti dei chunk.
        Restituisce una lista di nodi con score.
        """
        pass

    @abstractmethod
    def search_vectors(self, query_vector: List[float], limit: int = 20, repo_id: str = None, branch: str = None) -> List[Dict[str, Any]]:
        """
        Esegue una ricerca semantica per similarità vettoriale (Cosine Similarity).
        - In SQLite: Simulata in-memory con Numpy.
        - In Postgres: Eseguita nativamente via pgvector.
        """
        pass

    @abstractmethod
    def get_context_neighbors(self, node_id: str) -> Dict[str, List[Dict[str, Any]]]:
        """
        Trova i vicini utili per il contesto dell'Agente.
        Restituisce: { "parents": [...], "calls": [...] }
        """
        pass

    

