from dataclasses import dataclass, field, asdict
from typing import List, Dict, Any, Optional

@dataclass
class Repository:
    """
    Rappresenta lo stato di una repository indicizzata.
    Corrisponde alla tabella 'repositories' nel DB.
    """
    id: str
    url: str
    name: str
    branch: str
    last_commit: str
    status: str       # 'indexing', 'completed', 'failed'
    updated_at: str
    local_path: Optional[str] = None # Path fisico al worktree
    
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

@dataclass
class FileRecord:
    id: str
    repo_id: str
    commit_hash: str
    file_hash: str
    path: str
    language: str
    size_bytes: int
    category: str
    indexed_at: str
    
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

@dataclass
class ChunkNode:
    id: str
    file_id: str
    file_path: str
    chunk_hash: str
    type: str
    start_line: int
    end_line: int
    byte_range: List[int]
    # Contiene tag, modificatori e tipo originale (es. {"tags": ["async"], "original_type": "method_definition"})
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

@dataclass
class ChunkContent:
    chunk_hash: str
    content: str
    
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

@dataclass
class CodeRelation:
    """
    Rappresenta un arco nel grafo.
    Supporta sia collegamento spaziale (range) che diretto (id).
    """
    source_file: str
    target_file: str
    relation_type: str
    
    # Coordinate Spaziali (Usate da SCIP)
    source_line: int = -1 
    target_line: int = -1
    source_byte_range: Optional[List[int]] = None
    target_byte_range: Optional[List[int]] = None
    
    # Coordinate Dirette (Usate da Parser/Tree-sitter) - NUOVI CAMPI
    source_id: Optional[str] = None
    target_id: Optional[str] = None
    
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

@dataclass
class ParsingResult:
    files: List[FileRecord]
    nodes: List[ChunkNode]
    contents: Dict[str, ChunkContent]
    relations: List[CodeRelation] = field(default_factory=list) # <--- NUOVO CAMPO

    def to_dict(self) -> Dict[str, Any]:
        return {
            "files": [f.to_dict() for f in self.files],
            "nodes": [n.to_dict() for n in self.nodes],
            "contents": [c.to_dict() for c in self.contents.values()],
            "relations": [r.to_dict() for r in self.relations]
        }
    

@dataclass
class RetrievedContext:
    """
    Rappresenta un singolo risultato di ricerca arricchito per l'Agente.
    Contiene il codice, il punteggio e il contesto grafo.
    """
    node_id: str
    file_path: str
    chunk_type: str        # es. "function", "class"
    content: str           # Il codice effettivo
    
    # Ranking Info
    score: float           # Score finale normalizzato (0-1 approx)
    retrieval_method: str  # "hybrid", "dense", "sparse"
    
    # Metadata di navigazione
    start_line: int
    end_line: int
    language: str = "python"
    repo_id: str = ""
    branch: str = "main"  
    
    # --- Context Enrichment (Graph) ---
    # Il contesto gerarchico (es. "class PaymentProcessor")
    parent_context: Optional[str] = None 
    
    # Definizioni usate nel chunk (es. firme di funzioni chiamate)
    outgoing_definitions: List[str] = field(default_factory=list) 

    
    
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def render(self) -> str:
        """Helper per visualizzare il contesto in un prompt LLM."""
        header = f"### File: {self.file_path} ({self.start_line}-{self.end_line}) [{self.chunk_type}]"
        context_str = ""
        if self.parent_context:
            context_str = f"\nContext: In {self.parent_context}"
        
        refs = ""
        if self.outgoing_definitions:
            refs = "\nReferences: " + ", ".join(self.outgoing_definitions)
            
        return f"{header}{context_str}\n```python\n{self.content}\n```{refs}\n"