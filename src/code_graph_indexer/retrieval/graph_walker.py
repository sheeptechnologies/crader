import logging
import json
from typing import Dict, Any, List, Optional
from ..storage.base import GraphStorage

logger = logging.getLogger(__name__)

class GraphWalker:
    """
    Responsabile dell'espansione del contesto navigando il Knowledge Graph.
    Trasforma un nodo isolato in un contesto ricco per l'Agente.
    """
    def __init__(self, storage: GraphStorage):
        self.storage = storage

    def expand_context(self, node_doc: Dict[str, Any]) -> Dict[str, Any]:
        """
        Arricchisce un documento nodo con informazioni strutturali (Parent)
        e relazionali (Chiamate/Dipendenze).
        """
        node_id = node_doc['id']
        
        # Interroga lo storage per i vicini
        neighbors = self.storage.get_context_neighbors(node_id)
        
        return {
            "parent_context": self._format_parent_context(neighbors.get('parents', [])),
            "outgoing_definitions": self._extract_outgoing_defs(neighbors.get('calls', []))
        }

    def _format_parent_context(self, parents: List[Dict]) -> Optional[str]:
        """
        Costruisce una stringa descrittiva del contenitore (Vertical Expansion).
        Es: "Inside class PaymentProcessor defined in src/payments.py (L10)"
        """
        if not parents:
            return None
            
        # Prendiamo il padre più diretto (il primo della lista ritornata dallo storage)
        p = parents[0]
        p_type = p.get('type', 'block')
        p_file = p.get('file_path', 'unknown')
        p_line = p.get('start_line', '?')
        
        # Se il padre è un modulo, è meno interessante se siamo già nel file
        if p_type == 'module':
            return None
            
        return f"Inside {p_type} defined in {p_file} (L{p_line})"

    def _extract_outgoing_defs(self, calls: List[Dict]) -> List[str]:
        """
        Estrae i simboli usati dal nodo (Horizontal Expansion).
        Filtra duplicati e simboli sconosciuti.
        """
        symbols = []
        seen = set()
        
        for call in calls:
            sym = call.get('symbol')
            # Filtriamo simboli troppo generici o vuoti
            if sym and sym != "unknown" and "<" not in sym:
                if sym not in seen:
                    symbols.append(sym)
                    seen.add(sym)
        
        # Limitiamo a 5 per non inquinare troppo il prompt dell'Agente
        return symbols[:5]