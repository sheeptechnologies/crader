import logging
from typing import Any, Dict, List, Optional

from .storage.base import GraphStorage

logger = logging.getLogger(__name__)


class CodeNavigator:
    """
    Structural Navigation and Impact Analysis Component.

    This component provides an abstraction layer for traversing the code property graph (CPG) stored in the database.
    It enables advanced code exploration features found in IDEs, such as "Go to Definition", "Find Usages",
    "Call Hierarchy", and linear file scrolling.

    **Capabilities:**
    *   **Linear Navigation**: Sequential reading of code chunks (Next/Previous) to reconstruct file flows.
    *   **Hierarchical Navigation**: Jumping to parent contexts (e.g., from Method -> Class -> Module).
    *   **Impact Analysis**: Tracing incoming references ("Who calls this?") to assess the scope of changes.
    *   **Dependency Analysis**: Tracing outgoing calls ("What does this call?") to understand external dependencies.
    *   **Visual Flow Construction**: Generating recursive JSON tree structures representing execution paths for UI visualization.

    Attributes:
        storage (GraphStorage): The storage backend implementing graph query primitives.
    """

    def __init__(self, storage: GraphStorage):
        self.storage = storage

    def _enrich_node_info(self, node_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Internal helper to normalize and enrich raw node data.

        This parses JSON metadata and extracts a human-readable "primary label" (e.g., 'class', 'function')
        from semantic matches, prioritizing functional roles over generic types.

        Args:
            node_data (Dict[str, Any]): Raw node dictionary from the database.

        Returns:
            Dict[str, Any]: The input dictionary enriched with a 'type' field and parsed 'metadata'.
        """
        if not node_data:
            return node_data

        meta = node_data.get("metadata", {})
        # If it comes from SQLite, it might be a string
        if isinstance(meta, str):
            import json

            try:
                meta = json.loads(meta)
            except Exception:
                meta = {}
            node_data["metadata"] = meta

        # Derive a readable Label (replacement for the old 'type')
        matches = meta.get("semantic_matches", [])
        primary_label = "Code Block"

        # Priority: Role > Type
        for m in matches:
            if m.get("category") == "role":
                primary_label = m.get("label") or m.get("value")
                break
            elif m.get("category") == "type":
                primary_label = m.get("label") or m.get("value")

        # Inject the 'type' (or 'label') field for client convenience
        node_data["type"] = primary_label
        return node_data

    def read_neighbor_chunk(self, node_id: str, direction: str = "next") -> Optional[Dict[str, Any]]:
        """
        Retrieves the immediately adjacent code chunk in the original source file.

        This facilitates linear scrolling through a file, moving from one semantic block to the next/descending one.

        Args:
            node_id (str): The ID of the current reference node.
            direction (str): Navigation direction, either "next" (downstream) or "prev" (upstream).

        Returns:
            Optional[Dict[str, Any]]: The adjacent node data, or None if the file boundary is reached.

        Raises:
            ValueError: If `direction` is invalid.
        """
        if direction not in ["next", "prev"]:
            raise ValueError("Direction must be 'next' or 'prev'.")

        chunk = self.storage.get_neighbor_chunk(node_id, direction)
        return self._enrich_node_info(chunk)

    def read_parent_chunk(self, node_id: str) -> Optional[Dict[str, Any]]:
        """
        Navigates up the AST hierarchy to find the enclosing container node.

        Example: If `node_id` points to a method, this returns the Class definition.
        If it points to a Class, it returns the Module (file) node.

        Args:
            node_id (str): The ID of the child node.

        Returns:
            Optional[Dict[str, Any]]: The parent node data, enriched with metadata.
        """
        neighbors = self.storage.get_context_neighbors(node_id)
        parents = neighbors.get("parents", [])

        if not parents:
            return None

        # Enrich the first parent found
        return self._enrich_node_info(parents[0])

    def analyze_impact(self, node_id: str, limit: int = 20) -> List[Dict[str, Any]]:
        """
        Performs Reverse Call Graph Analysis ("Who calls me?").

        Identifies all code components that hold a dependency on the target node. This is critical for
        refactoring safety and understanding the ripple effects of a change.

        Args:
            node_id (str): The ID of the node to analyze.
            limit (int): Cap on the number of results.

        Returns:
            List[Dict[str, Any]]: A list of referencing nodes (callers).
        """
        logger.info(f"🕸️ Analyzing impact for: {node_id}")
        return self.storage.get_incoming_references(node_id, limit)

    def analyze_dependencies(self, node_id: str) -> List[Dict[str, Any]]:
        """
        Performs Forward Call Graph Analysis ("Who do I call?").

        Identifies all external symbols (functions, classes, constants) that are used or invoked by the target node.

        Args:
            node_id (str): The ID of the node to analyze.

        Returns:
            List[Dict[str, Any]]: A list of called/referenced nodes (callees).
        """
        logger.info(f"🕸️ Analyzing dependencies for: {node_id}")
        # 1. Retrieve parent metadata (O(1))
        nav = self.storage.get_neighbor_metadata(node_id)
        parent_info = nav.get("parent")
        if not parent_info:
            return None

        # 2. Retrieve content
        # Note: We might want to use a specific method to get only the signature
        # but for now we read the chunk.

        # Optimization: We could use get_chunk_by_id if implemented,
        # here we rely on the generic reader or do a direct query.
        return self.storage.get_outgoing_calls(node_id)

    def visualize_pipeline(self, node_id: str, max_depth: int = 2) -> Dict[str, Any]:
        """
        Generates a hierarchical structure representing the execution flow starting from a node.

        This constructs a JSON-serializable tree suitable for UI visualization (e.g., a node-link diagram or nested list),
        recursively tracing usage relationships up to a specified depth.

        Args:
            node_id (str): The root node ID for the visualization.
            max_depth (int): The recursion limit to prevent massive graph dumps (and cycle issues).

        Returns:
            Dict[str, Any]: A dictionary representing the call tree rooted at `node_id`.
        """
        logger.info(f"🕸️ Traversing pipeline for: {node_id}")

        def _walk(curr_id, depth):
            if depth > max_depth:
                return None

            calls = self.storage.get_outgoing_calls(curr_id, limit=10)
            if not calls:
                return {}

            tree = {}
            for call in calls:
                child_data = {
                    "file": call["file"],
                    "type": call["relation"],  # Fixed: use 'relation' instead of 'target_type'
                    "symbol": call.get("symbol"),
                    "children": _walk(call["target_id"], depth + 1),
                }
                tree[call["target_id"]] = child_data
            return tree

        return {"root_node": node_id, "call_graph": _walk(node_id, 1)}
