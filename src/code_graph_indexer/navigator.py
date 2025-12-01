import logging
from typing import Dict, Any, List, Optional
from .storage.base import GraphStorage

logger = logging.getLogger(__name__)

class CodeNavigator:
    """
    Facade per l'esplorazione strutturale (Impact Analysis, Call Graphs, Scrolling).
    Restituisce dati strutturati (Nodi/Relazioni) dal DB.
    """
    
    def __init__(self, storage: GraphStorage):
        self.storage = storage

    def read_neighbor_chunk(self, node_id: str, direction: str = "next") -> Optional[Dict[str, Any]]:
        """
        Restituisce il dizionario del chunk adiacente.
        Returns: Dict del nodo o None se non esiste.
        """
        if direction not in ["next", "prev"]:
            raise ValueError("Direction must be 'next' or 'prev'.")
            
        chunk = self.storage.get_neighbor_chunk(node_id, direction)
        return chunk # Già un dict o None dallo storage

    def read_parent_chunk(self, node_id: str) -> Optional[Dict[str, Any]]:
        """
        Trova il contenitore logico (Classe/Modulo) del chunk corrente.
        Returns: Dict del nodo padre o None.
        """
        neighbors = self.storage.get_context_neighbors(node_id)
        parents = neighbors.get("parents", [])
        
        if not parents:
            return None
            
        # Restituisce il primo genitore (il più diretto)
        # Nota: il dict tornato da get_context_neighbors potrebbe non avere il 'content'.
        # Se serve il contenuto del padre, si può fare una fetch extra qui o lasciarlo al chiamante.
        # Per ora ritorniamo i metadati strutturali.
        return parents[0]

    def analyze_impact(self, node_id: str, limit: int = 20) -> List[Dict[str, Any]]:
        """
        Trova chi dipende da questo nodo (Reverse Dependency).
        Returns: Lista di referenze raggruppate o piatte.
        """
        logger.info(f"🕸️ Analyzing impact for: {node_id}")
        refs = self.storage.get_incoming_references(node_id, limit)
        
        # Qui ritorniamo la lista raw arricchita, lasciando al client il raggruppamento
        # se necessario, oppure manteniamo il raggruppamento se utile logicamente.
        # Ritornare la lista piatta è più flessibile ("raw data").
        return refs

    def visualize_pipeline(self, node_id: str, max_depth: int = 2) -> Dict[str, Any]:
        """
        Esplora le chiamate in uscita (Forward Dependency).
        Returns: Struttura ad albero (Dict annidato).
        """
        logger.info(f"🕸️ Traversing pipeline for: {node_id}")
        
        def _walk(curr_id, depth):
            if depth > max_depth: return None # Stop recursion marker
            
            calls = self.storage.get_outgoing_calls(curr_id, limit=10)
            if not calls: return {}
            
            tree = {}
            for call in calls:
                # Usiamo un oggetto strutturato come valore, non solo una stringa
                child_data = {
                    "file": call['file'],
                    "type": call['target_type'],
                    "symbol": call.get('symbol'),
                    "children": _walk(call['target_id'], depth + 1)
                }
                tree[call['target_id']] = child_data
            return tree

        return {
            "root_node": node_id,
            "call_graph": _walk(node_id, 1)
        }