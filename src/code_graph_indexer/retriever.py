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
    """
    Facade principale per la ricerca semantica e strutturale.
    Richiede obbligatoriamente repo_id (Instance ID) per garantire l'isolamento dei dati.
    """
    
    def __init__(self, storage: GraphStorage, embedder: EmbeddingProvider):
        self.storage = storage
        self.embedder = embedder
        self.walker = GraphWalker(storage)

    def retrieve(self, query: str, repo_id: str, limit: int = 10, strategy: str = "hybrid") -> List[RetrievedContext]:
        """
        Esegue la ricerca e restituisce contesti arricchiti.
        
        Args:
            query: La domanda in linguaggio naturale.
            repo_id: OBBLIGATORIO. L'ID univoco dell'istanza repository (Repo + Branch).
            limit: Numero max risultati.
            strategy: "hybrid", "vector", "keyword".
        """
        if not repo_id:
            raise ValueError("Il parametro 'repo_id' è obbligatorio per garantire l'isolamento della ricerca.")

        logger.info(f"🔎 Retrieving: '{query}' (RepoID: {repo_id})")
        
        candidates = {}
        fetch_limit = limit * 2 if strategy == "hybrid" else limit
        
        # 1. Esecuzione Strategie
        # Passiamo branch=None perché il repo_id (UUID) è già specifico per il branch corrente.
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

        if not candidates:
            return []

        # 2. Reranking
        if strategy == "hybrid":
            ranked_docs = reciprocal_rank_fusion(candidates)
        else:
            ranked_docs = sorted(candidates.values(), key=lambda x: x.get('score', 0), reverse=True)

        # 3. Arricchimento
        return self._build_response(ranked_docs[:limit])

    def _build_response(self, docs: List[dict]) -> List[RetrievedContext]:
        results = []
        for doc in docs:
            # L'espansione del grafo usa l'ID del nodo, che è globale
            ctx_info = self.walker.expand_context(doc)
            
            methods = "+".join(sorted(list(doc.get('methods', ['unknown']))))
            score = doc.get('final_rrf_score', doc.get('score', 0.0))
            
            # Recuperiamo il nome del branch dai metadati del documento (salvati a DB)
            # Non serve più passarlo come input, è intrinseco nel dato.
            doc_branch = doc.get('branch', 'unknown')

            results.append(RetrievedContext(
                node_id=doc['id'],
                file_path=doc.get('file_path', 'unknown'),
                chunk_type=doc.get('type', 'code'),
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