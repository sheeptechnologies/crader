import logging
from typing import List, Optional, Dict, Any

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
    Richiede obbligatoriamente repo_id per garantire l'isolamento dei dati.
    """
    
    def __init__(self, storage: GraphStorage, embedder: EmbeddingProvider):
        self.storage = storage
        self.embedder = embedder
        self.walker = GraphWalker(storage)

    def retrieve(self, query: str, repo_id: str, limit: int = 10, strategy: str = "hybrid", 
                 filters: Dict[str, Any] = None) -> List[RetrievedContext]:
        """
        Esegue la ricerca e restituisce contesti arricchiti.
        
        Args:
            query: La domanda in linguaggio naturale.
            repo_id: OBBLIGATORIO. L'ID della repository in cui cercare.
            limit: Numero max risultati.
            strategy: "hybrid", "vector", "keyword".
            filters: Dizionario opzionale per filtri avanzati.
                     Es: {"path_prefix": "src/auth", "role": "entry_point", "language": "python"}
        """
        if not repo_id:
            raise ValueError("Il parametro 'repo_id' è obbligatorio per garantire l'isolamento della ricerca.")

        repo_id = str(repo_id)
        
        # Logga anche i filtri se presenti
        filter_log = f" | Filters: {filters}" if filters else ""
        logger.info(f"🔎 Retrieving: '{query}' (Repo: {repo_id[:8]}...){filter_log}")
        
        candidates = {}
        fetch_limit = limit * 2 if strategy == "hybrid" else limit
        
        # 1. Esecuzione Strategie
        if strategy in ["hybrid", "vector"]:
            SearchExecutor.vector_search(
                self.storage, self.embedder, query, fetch_limit, 
                repo_id=repo_id, branch=None, 
                filters=filters, # [NEW] Passiamo i filtri
                candidates=candidates
            )
            
        if strategy in ["hybrid", "keyword"]:
            SearchExecutor.keyword_search(
                self.storage, query, fetch_limit, 
                repo_id=repo_id, branch=None, 
                filters=filters, # [NEW] Passiamo i filtri
                candidates=candidates
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
            ctx_info = self.walker.expand_context(doc)
            
            meta = doc.get('metadata', {})
            # Compatibilità se meta è stringa
            if isinstance(meta, str):
                import json
                try: meta = json.loads(meta)
                except: meta = {}
            
            labels = []
            matches = meta.get('semantic_matches', [])
            for m in matches:
                label = m.get('label') or m.get('value')
                if label: labels.append(label)
            
            if not labels: labels = ["Code Block"]

            # [NEW] Recupero Nav Hints
            nav_hints = {}
            if hasattr(self.storage, 'get_neighbor_metadata'):
                nav_hints = self.storage.get_neighbor_metadata(doc['id'])

            results.append(RetrievedContext(
                node_id=doc['id'],
                file_path=doc.get('file_path', 'unknown'),
                semantic_labels=list(set(labels)),
                content=doc.get('content', ''),
                score=doc.get('final_rrf_score', doc.get('score', 0.0)),
                retrieval_method="+".join(sorted(list(doc.get('methods', ['unknown'])))),
                start_line=doc.get('start_line', 0),
                end_line=doc.get('end_line', 0),
                repo_id=doc.get('repo_id', ''),
                branch=doc.get('branch', 'main'),
                parent_context=ctx_info['parent_context'],
                outgoing_definitions=ctx_info['outgoing_definitions'],
                
                # [NEW] Nuovi campi popolati
                language=doc.get('language', 'text'),
                nav_hints=nav_hints
            ))
        return results