import logging
from typing import Dict, Any, Optional, List
from ..storage.base import GraphStorage
from ..providers.embedding import EmbeddingProvider

logger = logging.getLogger(__name__)

class SearchExecutor:
    """Helper per eseguire le strategie di ricerca grezze con filtri."""
    
    @staticmethod
    def vector_search(storage: GraphStorage, embedder: EmbeddingProvider, 
                     query: str, limit: int, repo_id: Optional[str], branch: Optional[str],
                     candidates: Dict[str, Any]):
        try:
            query_vec = embedder.embed([query])[0]
            # Passiamo branch allo storage
            results = storage.search_vectors(query_vec, limit, repo_id, branch)
            SearchExecutor._accumulate(candidates, results, "vector")
        except Exception as e:
            logger.error(f"❌ Vector search failed: {e}")

    @staticmethod
    def keyword_search(storage: GraphStorage, query: str, limit: int, 
                      repo_id: Optional[str], branch: Optional[str], 
                      candidates: Dict[str, Any]):
        try:
            # Passiamo branch allo storage
            results = storage.search_fts(query, limit, repo_id, branch)
            SearchExecutor._accumulate(candidates, results, "keyword")
        except Exception as e:
            logger.error(f"❌ Keyword search failed: {e}")

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