import logging
from typing import Dict, Any, List, Optional
from .storage.base import GraphStorage

logger = logging.getLogger(__name__)

class CodeNavigator:
    """
    Component for Graph Navigation.
    Provides methods to traverse relationships (Call Graph, Dependencies)
    and retrieve context around nodes.
    """
    
    def __init__(self, storage: GraphStorage):
        self.storage = storage

    def _enrich_node_info(self, node_data: Dict[str, Any]) -> Dict[str, Any]:
        """Helper to extract a readable type from semantic metadata."""
        if not node_data: return node_data
        
        meta = node_data.get('metadata', {})
        # If it comes from SQLite, it might be a string
        if isinstance(meta, str):
            import json
            try: meta = json.loads(meta)
            except: meta = {}
            node_data['metadata'] = meta

        # Derive a readable Label (replacement for the old 'type')
        matches = meta.get('semantic_matches', [])
        primary_label = "Code Block"
        
        # Priority: Role > Type
        for m in matches:
            if m.get('category') == 'role':
                primary_label = m.get('label') or m.get('value')
                break
            elif m.get('category') == 'type':
                primary_label = m.get('label') or m.get('value')
        
        # Inject the 'type' (or 'label') field for client convenience
        node_data['type'] = primary_label
        return node_data

    def read_neighbor_chunk(self, node_id: str, direction: str = "next") -> Optional[Dict[str, Any]]:
        if direction not in ["next", "prev"]:
            raise ValueError("Direction must be 'next' or 'prev'.")
            
        chunk = self.storage.get_neighbor_chunk(node_id, direction)
        return self._enrich_node_info(chunk)

    def read_parent_chunk(self, node_id: str) -> Optional[Dict[str, Any]]:
        """
        Reads the parent node (e.g., the Class containing the Method, or the Module).
        """
        neighbors = self.storage.get_context_neighbors(node_id)
        parents = neighbors.get("parents", [])
        
        if not parents: return None
        
        # Enrich the first parent found
        return self._enrich_node_info(parents[0])

    def analyze_impact(self, node_id: str, limit: int = 20) -> List[Dict[str, Any]]:
        """
        Finds "Who calls me?" (Reverse Call Graph).
        """
        logger.info(f"🕸️ Analyzing impact for: {node_id}")
        return self.storage.get_incoming_references(node_id, limit)

    def analyze_dependencies(self, node_id: str) -> List[Dict[str, Any]]:
        """
        Finds "Who do I call?" (Forward Call Graph).
        """
        logger.info(f"🕸️ Analyzing dependencies for: {node_id}")
        # 1. Retrieve parent metadata (O(1))
        nav = self.storage.get_neighbor_metadata(node_id)
        parent_info = nav.get("parent")
        if not parent_info: return None
        
        # 2. Retrieve content
        # Note: We might want to use a specific method to get only the signature
        # but for now we read the chunk.
        
        # Optimization: We could use get_chunk_by_id if implemented, 
        # here we rely on the generic reader or do a direct query.
        return self.storage.get_outgoing_calls(node_id)

    def visualize_pipeline(self, node_id: str, max_depth: int = 2) -> Dict[str, Any]:
        """
        Produces a visualization (JSON/Structure) of the call flow.
        Useful for "Explain this flow".
        depth: How deep to go in the call graph.
        """
        logger.info(f"🕸️ Traversing pipeline for: {node_id}")
        
        def _walk(curr_id, depth):
            if depth > max_depth: return None 
            
            calls = self.storage.get_outgoing_calls(curr_id, limit=10)
            if not calls: return {}
            
            tree = {}
            for call in calls:
                child_data = {
                    "file": call['file'],
                    "type": call['relation'], # Fixed: use 'relation' instead of 'target_type'
                    "symbol": call.get('symbol'),
                    "children": _walk(call['target_id'], depth + 1)
                }
                tree[call['target_id']] = child_data
            return tree

        return {
            "root_node": node_id,
            "call_graph": _walk(node_id, 1)
        }