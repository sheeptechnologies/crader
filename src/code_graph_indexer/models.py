from dataclasses import dataclass, field, asdict
from typing import List, Dict, Any, Optional
import datetime

@dataclass
class Repository:
    """
    Rappresenta l'identità stabile di un progetto monitorato.
    Non contiene stato mutevole (come lo stato di parsing), che è delegato agli Snapshots.
    """
    id: str
    url: str
    name: str
    branch: str
    
    # Puntatore allo snapshot attualmente "LIVE" (Ready to serve)
    # Se None, la repo è registrata ma non ha ancora dati pronti.
    current_snapshot_id: Optional[str] = None
    
    created_at: Optional[datetime.datetime] = None
    updated_at: Optional[datetime.datetime] = None
    
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

@dataclass
class Snapshot:
    """
    Rappresenta uno stato immutabile del codice in un preciso istante (Commit).
    Gestisce il ciclo di vita dell'indicizzazione (indexing -> completed).
    """
    id: str
    repository_id: str
    commit_hash: str
    
    # Status: 'pending', 'indexing', 'completed', 'failed'
    status: str
    
    created_at: datetime.datetime
    completed_at: Optional[datetime.datetime] = None
    
    # Metadati opzionali per statistiche (es. {"files_count": 50, "nodes_count": 2000})
    stats: Dict[str, Any] = field(default_factory=dict)
    
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

@dataclass
class FileRecord:
    id: str
    # [CHANGE] Punta allo Snapshot, non più alla Repository generica.
    # Questo garantisce che il file appartenga a UNA versione specifica.
    snapshot_id: str 
    
    commit_hash: str
    file_hash: str
    path: str
    language: str
    size_bytes: int
    category: str

    indexed_at: str
    
    parsing_status: str = "success"
    parsing_error: Optional[str] = None
    
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
    
    source_line: int = -1 
    target_line: int = -1
    source_byte_range: Optional[List[int]] = None
    target_byte_range: Optional[List[int]] = None
    
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
    node_id: str
    file_path: str
    content: str
    
    semantic_labels: List[str] = field(default_factory=list)
    
    score: float = 0.0
    retrieval_method: str = "unknown"
    
    start_line: int = 0
    end_line: int = 0
    
    repo_id: str = ""
    snapshot_id: str = ""
    branch: str = "main"  
    
    parent_context: Optional[str] = None
    outgoing_definitions: List[str] = field(default_factory=list) 

    language: str = "text"
    nav_hints: Dict[str, Any] = field(default_factory=dict)
    
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def render(self) -> str:
        # Logica di render invariata (omessa per brevità, è identica a prima)
        # ... (Mantieni il metodo render esistente)
        path_str = self.file_path
        if self.nav_hints.get("parent"):
            p_label = self.nav_hints["parent"].get("label", "Container")
            path_str += f" > {p_label}"

        labels_block = ""
        if self.semantic_labels:
            labels_block = " ".join([f"[{l}]" for l in self.semantic_labels])
        else:
            labels_block = "[Code Block]"

        out = []
        out.append(f"FILE: {path_str} (L{self.start_line}-{self.end_line})")
        out.append(f"LABELS: {labels_block}")
        out.append(f"NODE ID: {self.node_id}")

        md_lang = self.language.lower()
        out.append(f"\n```{md_lang}")
        out.append(self.content)
        out.append("```")

        if self.outgoing_definitions:
            out.append(f"\nRELATIONS:")
            for ref in self.outgoing_definitions[:5]:
                 out.append(f"- {ref}")
            if len(self.outgoing_definitions) > 5:
                out.append(f"- ... ({len(self.outgoing_definitions)-5} more)")

        navs = []
        if self.nav_hints.get("parent"):
            p = self.nav_hints["parent"]
            navs.append(f"SEMANTIC_PARENT_CHUNK: {p['label']} (ID: {p['id']})")
        else:
            navs.append(f"SEMANTIC_PARENT_CHUNK: None")
            
        if self.nav_hints.get("prev"):
            p = self.nav_hints["prev"]
            navs.append(f"PREV_FILE_CHUNK: {p['label']} (ID: {p['id']})")
        else:
            navs.append(f"PREV_FILE_CHUNK: None")

        if self.nav_hints.get("next"):
            n = self.nav_hints["next"]
            navs.append(f"NEXT_FILE_CHUNK: {n['label']} (ID: {n['id']})")
        else:
            navs.append(f"NEXT_FILE_CHUNK: None")
            
        if navs:
            out.append("\n[CODE NAVIGATION]:")
            out.extend(navs)

        return "\n".join(out) + "\n"