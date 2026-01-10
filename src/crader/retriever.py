import logging
from typing import Any, Dict, List, Optional

from .models import RetrievedContext
from .providers.embedding import EmbeddingProvider
from .retrieval.graph_walker import GraphWalker
from .retrieval.rankers import reciprocal_rank_fusion
from .retrieval.searcher import SearchExecutor
from .storage.postgres import PostgresGraphStorage

logger = logging.getLogger(__name__)


class CodeRetriever:
    """
    The Central Facade for Intelligent Code Retrieval.

    This class serves as the primary entry point for all semantic search and context retrieval operations
    within the system. It abstracts the complexity of hybrid search, implementing a "Read-Committed" consistency model
    by ensuring all queries are targeted against a specific, immutable Snapshot (either explicitly pinned or the latest active one).

    **Core Responsibilities:**
    *   **Snapshot Resolution**: Automatically resolves the target code state (Snapshot ID) from the Repository ID.
    *   **Hybrid Search Orchestration**: Coordinates Vector Search (semantic similarity) and Keyword Search (BM25/FTS) via `SearchExecutor`.
    *   **Result Fusion**: Implements Reciprocal Rank Fusion (RRF) to merge and re-rank results from disjoint search strategies.
    *   **Context Enrichment**: Uses `GraphWalker` to expand raw search hits with structural context (parent classes, file scope).
    *   **Navigation Hints**: Attaches metadata to enable seamless "Jump to Definition" or "Scroll" capabilities in the UI.

    Attributes:
        storage (PostgresGraphStorage): The low-level interface to the graph database.
        embedder (EmbeddingProvider): The provider used to embed query strings for vector search.
        walker (GraphWalker): Helper component for traversing the graph to build context.
    """

    def __init__(self, storage: PostgresGraphStorage, embedder: EmbeddingProvider):
        self.storage = storage
        self.embedder = embedder
        self.walker = GraphWalker(storage)

    def retrieve(
        self,
        query: str,
        repo_id: str,
        snapshot_id: Optional[str] = None,
        limit: int = 10,
        strategy: str = "hybrid",
        filters: Dict[str, Any] = None,
    ) -> List[RetrievedContext]:
        """
        Executes a high-fidelity search operation against the codebase.

        This method orchestrates the retrieval pipeline, ensuring consistency by resolving a concrete Snapshot ID
        before dispatching queries. It supports filtering by language, file path, and semantic role.

        **Pipeline Steps:**
        1.  **Resolution**: Determines the `target_snapshot_id`. If `snapshot_id` is None, fetches the current active snapshot for `repo_id`.
        2.  **Dispatch**: Delegates parallel execution of Vector and Keyword search strategies to `SearchExecutor`.
        3.  **Candidates Collection**: Aggregates raw matches from enabled strategies into a unified candidate pool.
        4.  **Rank Fusion**: Applies Reciprocal Rank Fusion (if strategy is 'hybrid') or standard scoring to order results.
        5.  **Rehydration**: Builds fully hydrated `RetrievedContext` objects with extended graph information.

        Args:
            query (str): The natural language query or valid code keywords.
            repo_id (str): The ID of the repository to search (required for resolution).
            snapshot_id (Optional[str]): The explicit snapshot ID to pin the search to. If None, uses the latest.
            limit (int): The maximum number of results to return.
            strategy (str): The search strategy to employ: 'hybrid' (default), 'vector', or 'keyword'.
            filters (Dict[str, Any]): Dictionary of metadata filters (e.g., `{'language': 'python', 'role': 'class'}`).

        Returns:
            List[RetrievedContext]: A ranked list of context-rich search results.

        Raises:
            ValueError: If neither `repo_id` nor `snapshot_id` is provided.
        """

        target_snapshot_id = snapshot_id

        # 1. Fallback to "Latest" if not pinned
        if not target_snapshot_id:
            if not repo_id:
                raise ValueError("You must provide repo_id (for latest) or snapshot_id (for pinned).")
            target_snapshot_id = self.storage.get_active_snapshot_id(str(repo_id))
            logger.info(f"🔄 Auto-resolution: Repo {repo_id} -> Snapshot {target_snapshot_id}")

        if not target_snapshot_id:
            logger.warning("⚠️ Retrieve impossibile: Nessuno snapshot attivo o valido.")
            return []

        # Log contestualizzato
        filter_log = f" | Filters: {filters}" if filters else ""
        context_mode = "PINNED" if snapshot_id else "LATEST"
        logger.info(f"🔎 Retrieving [{context_mode}]: '{query}' su Snap {target_snapshot_id[:8]}...{filter_log}")

        candidates = {}
        fetch_limit = limit * 2 if strategy == "hybrid" else limit

        # 2. Execution Strategies (Always with target_snapshot_id)
        if strategy in ["hybrid", "vector"]:
            SearchExecutor.vector_search(
                self.storage,
                self.embedder,
                query,
                fetch_limit,
                snapshot_id=target_snapshot_id,  # [CRITICAL] We use the resolved ID
                filters=filters,
                candidates=candidates,
            )

        if strategy in ["hybrid", "keyword"]:
            SearchExecutor.keyword_search(
                self.storage,
                query,
                fetch_limit,
                snapshot_id=target_snapshot_id,  # [FIX] Now we mandatory pass it
                repo_id=str(repo_id) if repo_id else None,
                filters=filters,
                candidates=candidates,
            )

        if not candidates:
            return []

        # 3. Reranking
        if strategy == "hybrid":
            ranked_docs = reciprocal_rank_fusion(candidates)
        else:
            ranked_docs = sorted(candidates.values(), key=lambda x: x.get("score", 0), reverse=True)

        # 4. Arricchimento
        return self._build_response(ranked_docs[:limit], target_snapshot_id)

    def _build_response(self, docs: List[dict], snapshot_id: str) -> List[RetrievedContext]:
        """
        Internal factory to construct rich RetrievedContext objects from raw search results.

        This method acts as the "rehydration" phase, where raw database rows are enriched with:
        *   **Context Expansion**: Calls `GraphWalker` to find parent nodes (e.g., enclosing class/function).
        *   **Label Normalization**: Extracts and normalizes semantic labels from JSON metadata.
        *   **Navigation Metadata**: Fetches hints for previous/next/parent nodes to support UI navigation.

        Args:
            docs (List[dict]): List of raw result dictionaries from the search strategies.
            snapshot_id (str): The snapshot ID context for these results.

        Returns:
            List[RetrievedContext]: The final list of domain objects ready for the client.
        """
        results = []
        for doc in docs:
            # Context expansion (GraphWalker)
            ctx_info = self.walker.expand_context(doc)

            meta = doc.get("metadata", {})
            if isinstance(meta, str):
                import json

                try:
                    meta = json.loads(meta)
                except Exception:
                    meta = {}

            labels = []
            matches = meta.get("semantic_matches", [])
            for m in matches:
                label = m.get("label") or m.get("value")
                if label:
                    labels.append(label)

            if not labels:
                labels = ["Code Block"]

            # Navigation
            nav_hints = {}
            if hasattr(self.storage, "get_neighbor_metadata"):
                nav_hints = self.storage.get_neighbor_metadata(doc["id"])

            results.append(
                RetrievedContext(
                    node_id=doc["id"],
                    snapshot_id=snapshot_id,
                    file_path=doc.get("file_path", "unknown"),
                    semantic_labels=list(set(labels)),
                    content=doc.get("content", ""),
                    score=doc.get("final_rrf_score", doc.get("score", 0.0)),
                    retrieval_method="+".join(sorted(list(doc.get("methods", ["unknown"])))),
                    start_line=doc.get("start_line", 0),
                    end_line=doc.get("end_line", 0),
                    repo_id=doc.get("repo_id", ""),
                    branch=doc.get("branch", "main"),
                    parent_context=ctx_info["parent_context"],
                    outgoing_definitions=ctx_info["outgoing_definitions"],
                    language=doc.get("language", "text"),
                    nav_hints=nav_hints,
                )
            )
        return results
