import logging
from typing import Dict, List

from ..models import ChunkContent, ChunkNode
from ..storage.base import GraphStorage
from .base import CodeRelation

logger = logging.getLogger(__name__)


class KnowledgeGraphBuilder:
    """
    Facade for Constructing the Code Property Graph (CPG).

    Serves as the high-level API for pushing parsed artifacts (Files, Nodes, Edges) into storage.

    **Key Responsibilities**:
    *   **Abstraction**: Hides specific storage implementation details from the `CodebaseIndexer`.
    *   **Resolution**: Handles the complex logic of linking edges (e.g., `calls`, `inherits`) by resolving byte-ranges to concrete `node_id`s, especially for cross-file references.
    *   **Search Optimization**: Aggregates metadata and content to populate the specific Full-Text Search index.
    """

    def __init__(self, storage: GraphStorage):
        self.storage = storage

    def add_files(self, files: List):
        self.storage.add_files(files)

    def add_chunks(self, chunks: List):
        self.storage.add_nodes(chunks)

    def add_contents(self, contents: List):
        self.storage.add_contents(contents)

    def build_search_documents(self, nodes: List[ChunkNode], contents: Dict[str, ChunkContent]) -> List[Dict]:
        """
        Generates search documents (FTS) from Nodes and Contents.
        Does NOT insert into DB.
        """
        search_batch = []
        for node in nodes:
            content_obj = contents.get(node.chunk_hash)
            raw_content = content_obj.content if content_obj else ""

            meta = node.metadata or {}
            matches = meta.get("semantic_matches", [])

            unique_tokens = set()
            for m in matches:
                if "value" in m:
                    unique_tokens.add(m["value"].lower())
                if "label" in m:
                    unique_tokens.update(m["label"].lower().split())

            tags_str = " ".join(sorted(unique_tokens))

            search_batch.append(
                {"node_id": node.id, "file_path": node.file_path, "tags": tags_str, "content": raw_content}
            )
        return search_batch

    def index_search_content(self, nodes: List[ChunkNode], contents: Dict[str, ChunkContent]):
        """
        Populates the Lexical Search Index (FTS).
        """
        search_batch = self.build_search_documents(nodes, contents)

        if hasattr(self.storage, "add_search_index"):
            self.storage.add_search_index(search_batch)

    def add_relations(self, relations: List[CodeRelation], snapshot_id: str = None):
        """
        Resolution and Insertion of Graph Edges.

        This method bridges the gap between the Abstract Syntax Tree (AST) relationships (often expressed as
        byte-ranges in a file) and the physical Graph Database (Node IDs).

        **Logic Flow**:
        1.  **Resolution**: Converts `(file_path, byte_range)` tuples into concrete `node_id`s using the Storage index.
        2.  **External Linking**: Handles external references (e.g. imports from stdlib) - *Currently a placeholder*.
        3.  **Persist**: Writes valid edges to the `edges` table.

        Args:
            relations: List of relationship descriptors found during parsing (SCIP/TreeSitter).
            snapshot_id: Required context to perform range-to-node lookups in the specific version of the code.
        """
        if not relations:
            return

        logger.info(f"Processing {len(relations)} relations (Context Snapshot: {snapshot_id})...")
        lookup_cache = {}

        # Helper to resolve ID from range
        def resolve_id(file_path, byte_range):
            if not snapshot_id:
                return None
            key = (file_path, tuple(byte_range))
            if key in lookup_cache:
                return lookup_cache[key]

            # Call to storage with snapshot_id
            nid = self.storage.find_chunk_id(file_path, byte_range, snapshot_id)
            if nid:
                lookup_cache[key] = nid
            return nid

        for rel in relations:
            # 1. Source Resolution
            if not rel.source_id:
                if rel.source_byte_range and len(rel.source_byte_range) == 2:
                    rel.source_id = resolve_id(rel.source_file, rel.source_byte_range)

            if not rel.source_id:
                continue

            # 2. Target Resolution
            if not rel.target_id:
                if rel.metadata.get("is_external"):
                    # Handle external nodes (e.g. standard library) - Placeholder
                    # self.storage.ensure_external_node(rel.target_file)
                    rel.target_id = rel.target_file  # Simplification for phantom nodes
                elif rel.target_byte_range and len(rel.target_byte_range) == 2:
                    rel.target_id = resolve_id(rel.target_file, rel.target_byte_range)

            # 3. Write Edge
            if rel.target_id and rel.source_id != rel.target_id:
                self.storage.add_edge(rel.source_id, rel.target_id, rel.relation_type, rel.metadata)

            if len(lookup_cache) > 20000:
                lookup_cache.clear()

    def get_stats(self):
        return self.storage.get_stats()
