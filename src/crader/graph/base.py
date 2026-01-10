from abc import ABC, abstractmethod
from typing import Any, Dict, List

from ..models import CodeRelation


class BaseGraphIndexer(ABC):
    """
    Classe base astratta per tutti gli indexer specializzati (SCIP, Frameworks, ecc.).
    """

    def __init__(self, repo_path: str):
        self.repo_path = repo_path

    @abstractmethod
    def extract_relations(self, chunk_map: Dict[str, Any]) -> List[CodeRelation]:
        """
        Analizza i dati e restituisce una lista di relazioni.
        chunk_map è passato per compatibilità, anche se SCIP lo ignora.
        """
        pass
