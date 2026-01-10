import datetime
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class Repository:
    """
    Project Identity Entity.

    Represents a stable "Container" for code versions.

    **Architectural Role**:
    *   **Stable Identity**: A repository persists across time, while its code changes constantly.
    *   **Versioning**: Tracks the `current_snapshot_id` which acts as the "HEAD" pointer for the search engine.
    *   **Authorization**: Used as a scope for tenant isolation in multi-tenant deployments.
    """

    id: str
    url: str
    name: str
    branch: str

    # Pointer to the currently "LIVE" snapshot (Ready to serve)
    # If None, the repository is registered but has no indexed data yet.
    current_snapshot_id: Optional[str] = None

    # Synchronization State
    reindex_requested_at: Optional[datetime.datetime] = None

    created_at: Optional[datetime.datetime] = None
    updated_at: Optional[datetime.datetime] = None

    def to_dict(self) -> Dict[str, Any]:
        """Serializes the entity to a dictionary for JSON/DB Storage."""
        return asdict(self)


@dataclass
class Snapshot:
    """
    Immutable Versioning Entity.

    Represents the state of a repository at a specific point in time (Commit SHA).

    **Lifecycle**:
    1.  `pending`: Created but empty.
    2.  `indexing`: Workers are actively parsing/embedding code.
    3.  `completed`: Fully indexed and ready for activation.
    4.  `failed`: Terminal error state.

    **Concurrency**:
    Multiple snapshots can exist for the same repository (e.g. historical analysis),
    but only one is usually 'Active' (pointed to by Repository).
    """

    id: str
    repository_id: str
    commit_hash: str

    # Status Enum: 'pending', 'indexing', 'completed', 'failed'
    status: str

    created_at: datetime.datetime
    completed_at: Optional[datetime.datetime] = None

    # Aggregated Metrics (e.g., {"files_count": 50, "nodes_count": 2000, "parse_duration_ms": 1500})
    stats: Dict[str, Any] = field(default_factory=dict)

    # File System manifest for O(1) lookups
    file_manifest: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class FileRecord:
    """
    Atomic File Object.

    Represents a single source file within a snapshot.
    It links the physical content (via `file_hash`) to the logical version (`snapshot_id`).
    Includes status flags for parsing errors (e.g. syntax errors, minified files).
    """

    id: str
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
    """
    The Fundamental Unit of the Code Graph.

    Also known as a "Symbol" or "Block".
    A ChunkNode represents a semantically meaningful range of code (e.g., a function, a class,
    or a standalone script block).

    **Properties**:
    *   **chunk_hash**: Content-addressable ID (used to skip re-processing if content is identical).
    *   **metadata**: JSON bag containing "Tags" (e.g. 'async', 'test') and "Semantic Captures" (e.g. 'role:controller').
    """

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
    """
    CAS Blob (Content Addressable Storage).

    Stores the actual text of a code chunk.
    Separated from `ChunkNode` to allow de-duplication: if 50 snapshots have the same function,
    we store the text only once.
    """

    chunk_hash: str
    content: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class CodeRelation:
    """
    Directed Edge in the Code Property Graph.

    Represents dependencies between code units:
    *   **Structural**: `child_of` (Function X is inside Class Y).
    *   **Semantic**: `calls`, `inherits`, `imports`.

    Can be "Resolved" (pointing to node IDs) or "Unresolved" (pointing to file + byte_range)
    during the early parsing phase.
    """

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
            "relations": [r.to_dict() for r in self.relations],
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
            labels_block = " ".join([f"[{lbl}]" for lbl in self.semantic_labels])
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
            out.append("\nRELATIONS:")
            for ref in self.outgoing_definitions[:5]:
                out.append(f"- {ref}")
            if len(self.outgoing_definitions) > 5:
                out.append(f"- ... ({len(self.outgoing_definitions) - 5} more)")

        navs = []
        if self.nav_hints.get("parent"):
            p = self.nav_hints["parent"]
            navs.append(f"SEMANTIC_PARENT_CHUNK: {p['label']} (ID: {p['id']})")
        else:
            navs.append("SEMANTIC_PARENT_CHUNK: None")

        if self.nav_hints.get("prev"):
            p = self.nav_hints["prev"]
            navs.append(f"PREV_FILE_CHUNK: {p['label']} (ID: {p['id']})")
        else:
            navs.append("PREV_FILE_CHUNK: None")

        if self.nav_hints.get("next"):
            n = self.nav_hints["next"]
            navs.append(f"NEXT_FILE_CHUNK: {n['label']} (ID: {n['id']})")
        else:
            navs.append("NEXT_FILE_CHUNK: None")

        if navs:
            out.append("\n[CODE NAVIGATION]:")
            out.extend(navs)

        return "\n".join(out) + "\n"
