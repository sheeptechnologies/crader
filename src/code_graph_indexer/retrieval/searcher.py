import logging
from typing import Dict, Any, Optional, List
from ..storage.base import GraphStorage
from ..providers.embedding import EmbeddingProvider

logger = logging.getLogger(__name__)

class SearchExecutor:
    """
    Helper per eseguire le strategie di ricerca.
    Allineato alla logica 'Snapshot Strict' (Niente repo_id verso lo storage).
    """
    
    @staticmethod
    def vector_search(storage: GraphStorage, embedder: EmbeddingProvider, 
                     query: str, limit: int, 
                     snapshot_id: str, # [CRITICAL] Il contesto primario
                     repo_id: Optional[str] = None, # Legacy/Audit only (non usato per storage)
                     filters: Optional[Dict[str, Any]] = None,
                     candidates: Dict[str, Any] = None):
        if candidates is None: candidates = {}
        try:
            query_vec = embedder.embed([query])[0]
            
            # [FIX] Rimossa chiamata con repo_id
            results = storage.search_vectors(
                query_vector=query_vec, 
                limit=limit, 
                snapshot_id=snapshot_id, 
                filters=filters
            )
            SearchExecutor._accumulate(candidates, results, "vector")
        except Exception as e:
            logger.error(f"❌ Vector search failed (Snap: {snapshot_id}): {e}")

    @staticmethod
    def keyword_search(storage: GraphStorage, query: str, limit: int, 
                      snapshot_id: str, 
                      repo_id: Optional[str] = None,
                      filters: Optional[Dict[str, Any]] = None,
                      candidates: Dict[str, Any] = None):
        if candidates is None: candidates = {}
        try:
            # [FIX] Rimossa chiamata con repo_id
            # Nota: search_fts in postgres.py ora richiede esplicitamente snapshot_id
            results = storage.search_fts(
                query=query, 
                limit=limit, 
                snapshot_id=snapshot_id,
                filters=filters
            )
            SearchExecutor._accumulate(candidates, results, "keyword")
        except Exception as e:
            logger.error(f"❌ Keyword search failed (Snap: {snapshot_id}): {e}")

    @staticmethod
    def _accumulate(candidates: Dict, results: List[Dict], method_name: str):
        for rank, item in enumerate(results):
            nid = item['id']
            if nid not in candidates:
                candidates[nid] = item.copy()
                candidates[nid]['methods'] = set()
                candidates[nid]['rrf_ranks'] = {}
            
            candidates[nid]['methods'].add(method_name)
            candidates[nid]['rrf_ranks'][method_name] = rank