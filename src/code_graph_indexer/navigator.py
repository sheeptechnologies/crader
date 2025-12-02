import logging
from typing import Dict, Any, List, Optional
from .storage.base import GraphStorage

logger = logging.getLogger(__name__)

class CodeNavigator:
    """
    Facade per l'esplorazione strutturale (Impact Analysis, Call Graphs, Scrolling).
    Restituisce dati strutturati arricchiti semanticamente.
    """
    
    def __init__(self, storage: GraphStorage):
        self.storage = storage

    def _enrich_node_info(self, node_data: Dict[str, Any]) -> Dict[str, Any]:
        """Helper per estrarre un tipo leggibile dai metadati semantici."""
        if not node_data: return node_data
        
        meta = node_data.get('metadata', {})
        # Se viene da SQLite potrebbe essere stringa
        if isinstance(meta, str):
            import json
            try: meta = json.loads(meta)
            except: meta = {}
            node_data['metadata'] = meta

        # Deriviamo una Label leggibile (sostituto del vecchio 'type')
        matches = meta.get('semantic_matches', [])
        primary_label = "Code Block"
        
        # Priorità: Role > Type
        for m in matches:
            if m.get('category') == 'role':
                primary_label = m.get('label') or m.get('value')
                break
            elif m.get('category') == 'type':
                primary_label = m.get('label') or m.get('value')
        
        # Iniettiamo il campo 'type' (o 'label') per comodità del client
        node_data['type'] = primary_label
        return node_data

    def read_neighbor_chunk(self, node_id: str, direction: str = "next") -> Optional[Dict[str, Any]]:
        if direction not in ["next", "prev"]:
            raise ValueError("Direction must be 'next' or 'prev'.")
            
        chunk = self.storage.get_neighbor_chunk(node_id, direction)
        return self._enrich_node_info(chunk)

    def read_parent_chunk(self, node_id: str) -> Optional[Dict[str, Any]]:
        neighbors = self.storage.get_context_neighbors(node_id)
        parents = neighbors.get("parents", [])
        
        if not parents: return None
        
        # Arricchiamo il primo genitore trovato
        return self._enrich_node_info(parents[0])

    def analyze_impact(self, node_id: str, limit: int = 20) -> List[Dict[str, Any]]:
        logger.info(f"🕸️ Analyzing impact for: {node_id}")
        return self.storage.get_incoming_references(node_id, limit)

    def visualize_pipeline(self, node_id: str, max_depth: int = 2) -> Dict[str, Any]:
        logger.info(f"🕸️ Traversing pipeline for: {node_id}")
        
        def _walk(curr_id, depth):
            if depth > max_depth: return None 
            
            calls = self.storage.get_outgoing_calls(curr_id, limit=10)
            if not calls: return {}
            
            tree = {}
            for call in calls:
                child_data = {
                    "file": call['file'],
                    "type": call['target_type'], # Questo viene dalla tabella edges, ok
                    "symbol": call.get('symbol'),
                    "children": _walk(call['target_id'], depth + 1)
                }
                tree[call['target_id']] = child_data
            return tree

        return {
            "root_node": node_id,
            "call_graph": _walk(node_id, 1)
        }