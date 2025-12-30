from dataclasses import dataclass, field, asdict
from typing import List, Dict, Any, Optional
import datetime

@dataclass
class Repository:
    """
    Represents the stable identity of a monitored project.
    Does not contain mutable state (like parsing state), which is delegated to Snapshots.
    """
    id: str
    url: str
    name: str
    branch: str
    
    # Pointer to the currently "LIVE" snapshot (Ready to serve)
    # If None, the repo is registered but has no data ready yet.
    current_snapshot_id: Optional[str] = None
    reindex_requested_at: Optional[datetime.datetime] = None # Dirty Flag

    created_at: Optional[datetime.datetime] = None
    updated_at: Optional[datetime.datetime] = None
    
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

@dataclass
class Snapshot:
    """
    Represents an immutable state of the code at a specific instant (Commit).
    Manages the indexing lifecycle (indexing -> completed).
    """
    id: str
    repository_id: str
    commit_hash: str
    
    # Status: 'pending', 'indexing', 'completed', 'failed'
    status: str
    
    created_at: datetime.datetime
    completed_at: Optional[datetime.datetime] = None
    
    # Optional metadata for statistics (e.g. {"files_count": 50, "nodes_count": 2000})
    stats: Dict[str, Any] = field(default_factory=dict)

    file_manifest: Dict[str, Any] = field(default_factory=dict)
    
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

@dataclass
class FileRecord:
    id: str
    # [CHANGE] Points to the Snapshot, not the generic Repository anymore.
    # This ensures the file belongs to a SPECIFIC version.
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
        # Render logic unchanged (omitted for brevity, same as before)
        # ... (Keep existing render method)
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