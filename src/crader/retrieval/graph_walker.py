import logging
from typing import Any, Dict, List, Optional

from ..storage.base import GraphStorage

logger = logging.getLogger(__name__)


class GraphWalker:
    """
    Knowledge Graph Navigator.

    Responsible for "Expanding" the context of a retrieved search result.
    When a user asks about a specific chunk of code, this component traverses the graph
    to fetch related nodes (Parent Classes, Called Functions, Type Definitions).

    **Goal**:
    Transform an isolated text match into a semantically connected subgraph.
    """

    def __init__(self, storage: GraphStorage):
        self.storage = storage

    def expand_context(self, node_doc: Dict[str, Any]) -> Dict[str, Any]:
        """
        Enriches a node document with its structural and relational neighborhood.

        Performs:
        1.  **Vertical Expansion**: identifying the implementation container (Class/Module).
        2.  **Horizontal Expansion**: identifying dependencies (Outgoing Calls) and usages.
        """
        node_id = node_doc["id"]

        # Query storage for neighbors
        neighbors = self.storage.get_context_neighbors(node_id)

        return {
            "parent_context": self._format_parent_context(neighbors.get("parents", [])),
            "outgoing_definitions": self._extract_outgoing_defs(neighbors.get("calls", [])),
        }

    def _format_parent_context(self, parents: List[Dict]) -> Optional[str]:
        """
        Constructs a human-readable "Where am I?" string (Vertical Context).

        Example: "Inside class PaymentProcessor defined in src/payments.py (L10)"
        """
        if not parents:
            return None

        # Get the most direct parent (the first in the list returned by storage)
        p = parents[0]
        p_type = p.get("type", "block")
        p_file = p.get("file_path", "unknown")
        p_line = p.get("start_line", "?")

        # If the parent is a module, it is less interesting if we are already in the file
        if p_type == "module":
            return None

        return f"Inside {p_type} defined in {p_file} (L{p_line})"

    def _extract_outgoing_defs(self, calls: List[Dict]) -> List[str]:
        """
        Identifies symbols used by the current node (Horizontal Context).
        Useful for providing definitions of functions called within the retrieved snippet.
        """
        symbols = []
        seen = set()

        for call in calls:
            sym = call.get("symbol")
            # Filter out symbols that are too generic or empty
            if sym and sym != "unknown" and "<" not in sym:
                if sym not in seen:
                    symbols.append(sym)
                    seen.add(sym)

        # Limit to 5 to avoid polluting the Agent's prompt too much
        return symbols[:5]
