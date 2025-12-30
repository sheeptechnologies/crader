from abc import ABC, abstractmethod
from typing import Iterator, List, Dict, Any, Optional, Generator, Tuple

class GraphStorage(ABC):
    """
    Interfaccia astratta per lo storage Enterprise (Snapshot-based).
    Definisce il contratto minimo per Identity, Write, Read e Graph Analysis.
    """
    
    # --- IDENTITY & SNAPSHOTS ---
    @abstractmethod
    def ensure_repository(self, url: str, branch: str, name: str) -> str: pass
    
    @abstractmethod
    def create_snapshot(self, repository_id: str, commit_hash: str) -> Tuple[str, bool]: pass
    
    @abstractmethod
    def activate_snapshot(self, repository_id: str, snapshot_id: str, stats: Dict[str, Any] = None): pass
    
    @abstractmethod
    def get_snapshot_manifest(self, snapshot_id: str) -> Dict[str, Any]: pass
       

    @abstractmethod
    def get_file_content_range(self, snapshot_id: str, file_path: str, start_line: int = None, end_line: int = None) -> Optional[str]:pass
    
    @abstractmethod
    def fail_snapshot(self, snapshot_id: str, error: str): pass
    
    @abstractmethod
    def prune_snapshot(self, snapshot_id: str): pass

    @abstractmethod
    def get_active_snapshot_id(self, repository_id: str) -> Optional[str]: pass
    
    @abstractmethod
    def get_repository(self, repo_id: str) -> Optional[Dict[str, Any]]: 
        """Serve al Reader (Legacy Mode) per trovare il path su disco."""
        pass

    # --- WRITE OPERATIONS ---
    @abstractmethod
    def add_files(self, files: List[Any]): pass
    @abstractmethod
    def add_nodes(self, nodes: List[Any]): pass
    @abstractmethod
    def add_contents(self, contents: List[Any]): pass
    @abstractmethod
    def add_edge(self, source_id: str, target_id: str, relation_type: str, metadata: Dict[str, Any]): pass
    
    @abstractmethod
    def add_search_index(self, search_docs: List[Dict[str, Any]]): pass

    @abstractmethod
    def save_embeddings(self, vector_documents: List[Dict[str, Any]]): pass

    # --- READ & RETRIEVAL ---
    @abstractmethod
    def find_chunk_id(self, file_path: str, byte_range: List[int], snapshot_id: str) -> Optional[str]: pass
    
    @abstractmethod
    def get_contents_bulk(self, chunk_hashes: List[str]) -> Dict[str, str]: pass

    @abstractmethod
    def get_vectors_by_hashes(self, vector_hashes: List[str], model_name: str) -> Dict[str, List[float]]: pass

    @abstractmethod
    def get_nodes_to_embed(self, snapshot_id: str, model_name: str, batch_size: int = 2000) -> Generator[Dict[str, Any], None, None]: pass

    @abstractmethod
    def search_fts(self, query: str, limit: int, snapshot_id: str, filters: Dict[str, Any] = None) -> List[Dict[str, Any]]: pass
    
    @abstractmethod
    def search_vectors(self, query_vector: List[float], limit: int, snapshot_id: str, filters: Dict[str, Any] = None) -> List[Dict[str, Any]]: pass

    # --- GRAPH NAVIGATION (Cruciale per Navigator) ---
    @abstractmethod
    def get_context_neighbors(self, node_id: str) -> Dict[str, List[Dict[str, Any]]]: pass
    
    @abstractmethod
    def get_neighbor_chunk(self, node_id: str, direction: str = "next") -> Optional[Dict[str, Any]]: pass
    
    @abstractmethod
    def get_incoming_references(self, target_node_id: str, limit: int = 50) -> List[Dict[str, Any]]: pass
    
    @abstractmethod
    def get_outgoing_calls(self, source_node_id: str, limit: int = 50) -> List[Dict[str, Any]]: pass
    
    @abstractmethod
    def get_incoming_definitions_bulk(self, node_ids: List[str]) -> Dict[str, List[str]]: pass
    
    @abstractmethod
    def get_neighbor_metadata(self, node_id: str) -> Dict[str, Any]: pass

    # --- UTILS ---
    @abstractmethod
    def get_stats(self) -> Dict[str, int]: pass
    @abstractmethod
    def close(self): pass



    @abstractmethod
    def prepare_embedding_staging(self): 
        """Crea la tabella temporanea per il caricamento massivo."""
        pass

    @abstractmethod
    def load_staging_data(self, data_generator: Iterator[Tuple]):
        """Carica i dati grezzi nella staging table via COPY stream."""
        pass

    @abstractmethod
    def backfill_staging_vectors(self) -> int:
        """Deduplica: Copia i vettori dagli snapshot passati alla staging table."""
        pass

    @abstractmethod
    def flush_staged_hits(self, snapshot_id: str) -> int:
        """
        Sposta i record completi (con vettore) dalla staging alla tabella finale.
        Ritorna il numero di vettori recuperati (Hits).
        """
        pass

    @abstractmethod
    def fetch_staging_delta(self, batch_size: int = 2000) -> Generator[List[Dict], None, None]:
        """Itera sui record rimasti in staging (quelli senza vettore) per calcolarli."""
        pass

    @abstractmethod
    def save_embeddings_direct(self, records: List[Dict[str, Any]]):
        """Salvataggio diretto (bypass staging) per i nuovi vettori calcolati."""
        pass