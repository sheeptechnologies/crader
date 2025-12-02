import logging
from typing import List, Optional

from .storage.base import GraphStorage
from .providers.embedding import EmbeddingProvider
from .models import RetrievedContext
from .retrieval.rankers import reciprocal_rank_fusion
from .retrieval.graph_walker import GraphWalker
from .retrieval import SearchExecutor

logger = logging.getLogger(__name__)

class CodeRetriever:
    def __init__(self, storage: GraphStorage, embedder: EmbeddingProvider):
        self.storage = storage
        self.embedder = embedder
        self.walker = GraphWalker(storage)

    def retrieve(self, query: str, repo_id: str, limit: int = 10, strategy: str = "hybrid") -> List[RetrievedContext]:
        if not repo_id:
            raise ValueError("Il parametro 'repo_id' è obbligatorio.")

        logger.info(f"🔎 Retrieving: '{query}' (RepoID: {repo_id})")
        
        candidates = {}
        fetch_limit = limit * 2 if strategy == "hybrid" else limit
        
        if strategy in ["hybrid", "vector"]:
            SearchExecutor.vector_search(
                self.storage, self.embedder, query, fetch_limit, 
                repo_id=repo_id, branch=None, candidates=candidates
            )
            
        if strategy in ["hybrid", "keyword"]:
            SearchExecutor.keyword_search(
                self.storage, query, fetch_limit, 
                repo_id=repo_id, branch=None, candidates=candidates
            )

        if not candidates: return []

        if strategy == "hybrid":
            ranked_docs = reciprocal_rank_fusion(candidates)
        else:
            ranked_docs = sorted(candidates.values(), key=lambda x: x.get('score', 0), reverse=True)

        return self._build_response(ranked_docs[:limit])

    def _build_response(self, docs: List[dict]) -> List[RetrievedContext]:
        results = []
        for doc in docs:
            ctx_info = self.walker.expand_context(doc)
            
            # Estrazione Label Semantiche dai metadati
            meta = doc.get('metadata', {})
            # Se viene da SQLite row grezza, potrebbe essere stringa o dict
            if isinstance(meta, str):
                import json
                try: meta = json.loads(meta)
                except: meta = {}
            
            labels = []
            matches = meta.get('semantic_matches', [])
            for m in matches:
                # Priorità: Label leggibile > Valore tecnico
                label = m.get('label') or m.get('value')
                if label: labels.append(label)
            
            if not labels: labels = ["Code Block"] # Fallback

            methods = "+".join(sorted(list(doc.get('methods', ['unknown']))))
            score = doc.get('final_rrf_score', doc.get('score', 0.0))
            doc_branch = doc.get('branch', 'main')

            results.append(RetrievedContext(
                node_id=doc['id'],
                file_path=doc.get('file_path', 'unknown'),
                semantic_labels=list(set(labels)), 
                content=doc.get('content', ''),
                score=score,
                retrieval_method=methods,
                start_line=doc.get('start_line', 0),
                end_line=doc.get('end_line', 0),
                repo_id=doc.get('repo_id', ''),
                branch=doc_branch,
                parent_context=ctx_info['parent_context'],
                outgoing_definitions=ctx_info['outgoing_definitions']
            ))
        return results