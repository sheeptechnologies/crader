from dataclasses import dataclass, field, asdict
from typing import List, Dict, Any, Optional

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