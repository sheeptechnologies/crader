from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional

@dataclass
class CodeRelation:
    """
    Rappresenta un arco (connessione) tra due punti del codice.
    """
    source_file: str
    target_file: str
    relation_type: str
    source_line: int = -1 
    target_line: int = -1
    source_byte_range: Optional[List[int]] = None
    target_byte_range: Optional[List[int]] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

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