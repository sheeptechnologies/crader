from abc import ABC, abstractmethod
from typing import Any, Dict, Generator, Iterator, List, Optional, Tuple


class GraphStorage(ABC):
    """
    Abstract Base Class (ABC) for Enterprise Graph Storage.

    Defines the contract for the Code Property Graph (CPG) persistence layer.
    It mandates support for Snapshot isolation, allowing concurrent indexing and retrieval operations.

    Any compliant backend (Postgres, SQLite) must implement these methods.
    """

    # --- IDENTITY & SNAPSHOTS ---
    @abstractmethod
    def ensure_repository(self, url: str, branch: str, name: str) -> str:
        pass

    @abstractmethod
    def create_snapshot(self, repository_id: str, commit_hash: str) -> Tuple[str, bool]:
        pass

    @abstractmethod
    def activate_snapshot(self, repository_id: str, snapshot_id: str, stats: Dict[str, Any] = None):
        pass

    @abstractmethod
    def get_snapshot_manifest(self, snapshot_id: str) -> Dict[str, Any]:
        pass

    @abstractmethod
    def get_file_content_range(
        self, snapshot_id: str, file_path: str, start_line: int = None, end_line: int = None
    ) -> Optional[str]:
        pass

    @abstractmethod
    def fail_snapshot(self, snapshot_id: str, error: str):
        pass

    @abstractmethod
    def prune_snapshot(self, snapshot_id: str):
        pass

    @abstractmethod
    def get_active_snapshot_id(self, repository_id: str) -> Optional[str]:
        pass

    @abstractmethod
    def get_repository(self, repo_id: str) -> Optional[Dict[str, Any]]:
        """
        Retrieves repository metadata (url, branch, current_snapshot).
        Used by the Reader to locate the physical path on disk (legacy mode) or logical volume refs.
        """
        pass

    # --- WRITE OPERATIONS ---
    @abstractmethod
    def add_files(self, files: List[Any]):
        pass

    @abstractmethod
    def add_nodes(self, nodes: List[Any]):
        pass

    @abstractmethod
    def add_contents(self, contents: List[Any]):
        pass

    @abstractmethod
    def add_edge(self, source_id: str, target_id: str, relation_type: str, metadata: Dict[str, Any]):
        pass

    @abstractmethod
    def add_search_index(self, search_docs: List[Dict[str, Any]]):
        pass

    @abstractmethod
    def save_embeddings(self, vector_documents: List[Dict[str, Any]]):
        pass

    # --- READ & RETRIEVAL ---
    @abstractmethod
    def find_chunk_id(self, file_path: str, byte_range: List[int], snapshot_id: str) -> Optional[str]:
        pass

    @abstractmethod
    def get_contents_bulk(self, chunk_hashes: List[str]) -> Dict[str, str]:
        pass

    @abstractmethod
    def get_vectors_by_hashes(self, vector_hashes: List[str], model_name: str) -> Dict[str, List[float]]:
        pass

    @abstractmethod
    def get_nodes_to_embed(
        self, snapshot_id: str, model_name: str, batch_size: int = 2000
    ) -> Generator[Dict[str, Any], None, None]:
        pass

    @abstractmethod
    def search_fts(
        self, query: str, limit: int, snapshot_id: str, filters: Dict[str, Any] = None
    ) -> List[Dict[str, Any]]:
        pass

    @abstractmethod
    def search_vectors(
        self, query_vector: List[float], limit: int, snapshot_id: str, filters: Dict[str, Any] = None
    ) -> List[Dict[str, Any]]:
        pass

    # --- GRAPH NAVIGATION (Crucial for Navigator) ---
    @abstractmethod
    def get_context_neighbors(self, node_id: str) -> Dict[str, List[Dict[str, Any]]]:
        pass

    @abstractmethod
    def get_neighbor_chunk(self, node_id: str, direction: str = "next") -> Optional[Dict[str, Any]]:
        pass

    @abstractmethod
    def get_incoming_references(self, target_node_id: str, limit: int = 50) -> List[Dict[str, Any]]:
        pass

    @abstractmethod
    def get_outgoing_calls(self, source_node_id: str, limit: int = 50) -> List[Dict[str, Any]]:
        pass

    @abstractmethod
    def get_incoming_definitions_bulk(self, node_ids: List[str]) -> Dict[str, List[str]]:
        pass

    @abstractmethod
    def get_neighbor_metadata(self, node_id: str) -> Dict[str, Any]:
        pass

    # --- UTILS ---
    @abstractmethod
    def get_stats(self) -> Dict[str, int]:
        pass

    @abstractmethod
    def close(self):
        pass

    @abstractmethod
    def prepare_embedding_staging(self):
        """
        Initializes an ephemeral staging area (e.g., UNLOGGED table) for high-speed massive insertion.
        Required for the vector embedding pipeline.
        """
        pass

    @abstractmethod
    def load_staging_data(self, data_generator: Iterator[Tuple]):
        """
        Bulk loads raw data tuples into the staging area.
        Should use the fastest available method (e.g., COPY protocol).
        """
        pass

    @abstractmethod
    def backfill_staging_vectors(self) -> int:
        """
        Deduplication Strategy: Checks if staged vectors already exist in history.
        Must fill the 'embedding' column in the staging table from historical data to save API cost.
        Returns the number of recovered vectors.
        """
        pass

    @abstractmethod
    def flush_staged_hits(self, snapshot_id: str) -> int:
        """
        Promotes fully resolved records (those with embeddings) from Staging to Production (Node Embeddings table).
        Returns the count of promoted items (Hits).
        """
        pass

    @abstractmethod
    def fetch_staging_delta(self, batch_size: int = 2000) -> Generator[List[Dict], None, None]:
        """
        Yields batches of staged records that are still missing embeddings (The Delta).
        These will be processed by external embedding workers.
        """
        pass

    @abstractmethod
    def save_embeddings_direct(self, records: List[Dict[str, Any]]):
        """
        Direct write path for new vectors calculated by workers.
        Bypasses staging and inserts directly into the production table (e.g., via INSERT ... ON CONFLICT).
        """
        pass
