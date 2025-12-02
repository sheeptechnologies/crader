from dataclasses import dataclass, field, asdict
from typing import List, Dict, Any, Optional

@dataclass
class Repository:
    id: str
    url: str
    name: str
    branch: str
    last_commit: str
    status: str
    updated_at: str
    local_path: Optional[str] = None
    
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
    start_line: int
    end_line: int
    byte_range: List[int]
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
    source_file: str
    target_file: str
    relation_type: str
    
    # Coordinate Spaziali (Usate da SCIP)
    source_line: int = -1 
    target_line: int = -1
    source_byte_range: Optional[List[int]] = None
    target_byte_range: Optional[List[int]] = None
    
    # Coordinate Dirette
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
    relations: List[CodeRelation] = field(default_factory=list)

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
    """
    node_id: str
    file_path: str
    content: str
    
    # [NEW] Sostituisce 'chunk_type' che era ambiguo
    # Es. ["Application Entry Point", "Function Definition"]
    semantic_labels: List[str] = field(default_factory=list)
    
    # Ranking Info
    score: float = 0.0
    retrieval_method: str = "unknown"
    
    # Metadata di navigazione
    start_line: int = 0
    end_line: int = 0
    repo_id: str = ""
    branch: str = "main"  
    
    # --- Context Enrichment (Graph) ---
    parent_context: Optional[str] = None 
    outgoing_definitions: List[str] = field(default_factory=list) 
    
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def render(self) -> str:
        """Helper per visualizzare il contesto in un prompt LLM."""
        labels_str = " | ".join(self.semantic_labels) if self.semantic_labels else "Code Block"
        header = f"### File: {self.file_path} (L{self.start_line}-{self.end_line}) [{labels_str}]"
        
        context_str = ""
        if self.parent_context:
            context_str = f"\nContext: In {self.parent_context}"
        
        refs = ""
        if self.outgoing_definitions:
            refs = "\nReferences: " + ", ".join(self.outgoing_definitions)
            
        return f"{header}{context_str}\n```python\n{self.content}\n```{refs}\n"