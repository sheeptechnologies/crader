import logging
from typing import List, Optional, Dict, Any

from .storage.postgres import PostgresGraphStorage
from .providers.embedding import EmbeddingProvider
from .models import RetrievedContext
from .retrieval.rankers import reciprocal_rank_fusion
from .retrieval.graph_walker import GraphWalker
from .retrieval.searcher import SearchExecutor

logger = logging.getLogger(__name__)

class CodeRetriever:
    """
    Facade principale per la ricerca semantica e strutturale.
    Implementa la logica "Read-Committed" sugli Snapshot attivi.
    """
    
    def __init__(self, storage: PostgresGraphStorage, embedder: EmbeddingProvider):
        self.storage = storage
        self.embedder = embedder
        self.walker = GraphWalker(storage)

    def retrieve(self, query: str, repo_id: str, snapshot_id: Optional[str] = None, 
                 limit: int = 10, strategy: str = "hybrid", 
                 filters: Dict[str, Any] = None) -> List[RetrievedContext]:
        """
        Esegue la ricerca puntando allo Snapshot ATTIVO della repository.
        """
        
        target_snapshot_id = snapshot_id
        
        # 1. Fallback su "Latest" se non pinnato
        if not target_snapshot_id:
            if not repo_id:
                 raise ValueError("Devi fornire repo_id (per latest) o snapshot_id (per pinned).")
            target_snapshot_id = self.storage.get_active_snapshot_id(str(repo_id))
            logger.info(f"🔄 Risoluzione Automatica: Repo {repo_id} -> Snapshot {target_snapshot_id}")
        
        if not target_snapshot_id:
            logger.warning(f"⚠️ Retrieve impossibile: Nessuno snapshot attivo o valido.")
            return []

        # Log contestualizzato
        filter_log = f" | Filters: {filters}" if filters else ""
        context_mode = "PINNED" if snapshot_id else "LATEST"
        logger.info(f"🔎 Retrieving [{context_mode}]: '{query}' su Snap {target_snapshot_id[:8]}...{filter_log}")
        
        candidates = {}
        fetch_limit = limit * 2 if strategy == "hybrid" else limit
        
        # 2. Esecuzione Strategie (Sempre con target_snapshot_id)
        if strategy in ["hybrid", "vector"]:
            SearchExecutor.vector_search(
                self.storage, self.embedder, query, fetch_limit, 
                snapshot_id=target_snapshot_id, # [CRITICAL] Usiamo l'ID risolto
                filters=filters,
                candidates=candidates
            )
            
        if strategy in ["hybrid", "keyword"]:
            SearchExecutor.keyword_search(
                self.storage, query, fetch_limit, 
                snapshot_id=target_snapshot_id, # [FIX] Ora lo passiamo obbligatoriamente
                repo_id=str(repo_id) if repo_id else None,
                filters=filters,
                candidates=candidates
            )

        if not candidates:
            return []

        # 3. Reranking
        if strategy == "hybrid":
            ranked_docs = reciprocal_rank_fusion(candidates)
        else:
            ranked_docs = sorted(candidates.values(), key=lambda x: x.get('score', 0), reverse=True)

        # 4. Arricchimento
        return self._build_response(ranked_docs[:limit], target_snapshot_id)

    def _build_response(self, docs: List[dict], snapshot_id: str) -> List[RetrievedContext]:
        results = []
        for doc in docs:
            # Espansione contesto (GraphWalker)
            ctx_info = self.walker.expand_context(doc)
            
            meta = doc.get('metadata', {})
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

            # Navigazione
            nav_hints = {}
            if hasattr(self.storage, 'get_neighbor_metadata'):
                nav_hints = self.storage.get_neighbor_metadata(doc['id'])

            results.append(RetrievedContext(
                node_id=doc['id'],
                snapshot_id=snapshot_id,
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
                language=doc.get('language', 'text'),
                nav_hints=nav_hints
            ))
        return results