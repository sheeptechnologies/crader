import logging
from typing import Any, Dict, List, Optional

from ..providers.embedding import EmbeddingProvider
from ..storage.base import GraphStorage

logger = logging.getLogger(__name__)


class SearchExecutor:
    """
    Orchestration Engine for Search Strategies.

    This static utility class is responsible for dispatching queries to different search backends
    (Vector Search, Full-Text Search) and accumulating the results into a unified candidate pool.

    **Key Principles:**
    *   **Snapshot Isolation**: Enforces searching strictly within a target `snapshot_id`.
    *   **Normalization**: Standardizes results from different backends into a common dictionary structure.
    *   **Side-Effect Accumulation**: Modifies a `candidates` dictionary in-place to prepare for Fusion (RRF).
    """

    @staticmethod
    def vector_search(
        storage: GraphStorage,
        embedder: EmbeddingProvider,
        query: str,
        limit: int,
        snapshot_id: str,
        repo_id: Optional[str] = None,
        filters: Optional[Dict[str, Any]] = None,
        candidates: Dict[str, Any] = None,
    ):
        """
        Executes Semantic Search (ANN) and accumulates results.

        1.  Computes the embedding vector for the `query` using `embedder`.
        2.  Delegates the ANN search to `storage.search_vectors`.
        3.  Updates the `candidates` pool with the results, tagging them with `method='vector'`.

        Args:
            storage: The data access layer.
            embedder: Identity provider for vectorization.
            query: The user's natural language query.
            limit: Max items to fetch.
            snapshot_id: The target snapshot UUID.
            repo_id: (Deprecated) Kept for interface compatibility, not used for logic.
            filters: Metadata filters.
            candidates: Mutable dictionary for result aggregation.
        """
        if candidates is None:
            candidates = {}
        try:
            query_vec = embedder.embed([query])[0]

            # Removed call with repo_id
            results = storage.search_vectors(
                query_vector=query_vec, limit=limit, snapshot_id=snapshot_id, filters=filters
            )
            SearchExecutor._accumulate(candidates, results, "vector")
        except Exception as e:
            logger.error(f"❌ Vector search failed (Snap: {snapshot_id}): {e}")

    @staticmethod
    def keyword_search(
        storage: GraphStorage,
        query: str,
        limit: int,
        snapshot_id: str,
        repo_id: Optional[str] = None,
        filters: Optional[Dict[str, Any]] = None,
        candidates: Dict[str, Any] = None,
    ):
        """
        Executes Lexical Search (FTS) and accumulates results.

        1.  Delegates the Full-Text Search to `storage.search_fts`.
        2.  Updates the `candidates` pool with results, tagging them with `method='keyword'`.

        Use Case: Finds exact matches, specific function names, or error codes that semantic search might miss.

        Args:
            storage: The data access layer.
            query: The keywords/tokens to fuzzy match.
            limit: Max items to fetch.
            snapshot_id: The target snapshot UUID.
            repo_id: (Deprecated) Unused.
            filters: Metadata filters.
            candidates: Mutable dictionary for result aggregation.
        """
        if candidates is None:
            candidates = {}
        try:
            # Removed call with repo_id
            # Note: search_fts in postgres.py now explicitly requires snapshot_id
            results = storage.search_fts(query=query, limit=limit, snapshot_id=snapshot_id, filters=filters)
            SearchExecutor._accumulate(candidates, results, "keyword")
        except Exception as e:
            logger.error(f"❌ Keyword search failed (Snap: {snapshot_id}): {e}")

    @staticmethod
    def _accumulate(candidates: Dict, results: List[Dict], method_name: str):
        for rank, item in enumerate(results):
            nid = item["id"]
            if nid not in candidates:
                candidates[nid] = item.copy()
                candidates[nid]["methods"] = set()
                candidates[nid]["rrf_ranks"] = {}

            candidates[nid]["methods"].add(method_name)
            candidates[nid]["rrf_ranks"][method_name] = rank
