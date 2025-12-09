import logging
import os
from typing import List, Dict, Optional, Any
from .storage.base import GraphStorage

logger = logging.getLogger(__name__)

class CodeReader:
    """
    Virtual Filesystem Reader (Manifest-Based).
    O(1) listing, Lazy Loading reading.
    """
    
    def __init__(self, storage: GraphStorage):
        self.storage = storage
        # Cache locale del manifest per sessione (opzionale, ma utile se l'oggetto Reader vive a lungo)
        self._manifest_cache = {}

    def _get_manifest(self, snapshot_id: str) -> Dict:
        if snapshot_id not in self._manifest_cache:
            self._manifest_cache[snapshot_id] = self.storage.get_snapshot_manifest(snapshot_id)
        return self._manifest_cache[snapshot_id]

    def read_file(self, snapshot_id: str, file_path: str, start_line: int = None, end_line: int = None) -> Dict[str, Any]:
        """
        Smart Read: Scarica solo i chunk necessari dal DB.
        """
        # Nota: Qui potremmo anche validare l'esistenza del file guardando il manifest prima di chiamare il DB
        content = self.storage.get_file_content_range(snapshot_id, file_path, start_line, end_line)
        
        if content is None:
            raise FileNotFoundError(f"File '{file_path}' non trovato nello snapshot {snapshot_id[:8]}")

        return {
            "file_path": file_path,
            "content": content,
            "start_line": start_line or 1,
            "end_line": end_line or "EOF"
        }

    def list_directory(self, snapshot_id: str, path: str = "") -> List[Dict[str, Any]]:
        """
        O(1) Listing usando il Manifest JSON pre-calcolato.
        """
        manifest = self._get_manifest(snapshot_id)
        
        # Navigazione nell'albero JSON
        current = manifest
        # Rimuoviamo slash iniziali/finali
        target_parts = [p for p in path.split('/') if p]
        
        try:
            # Scendiamo nell'albero
            for part in target_parts:
                current = current["children"][part]
                if current["type"] != "dir":
                    raise NotADirectoryError(f"{path} non è una directory.")
        except KeyError:
            # Se un pezzo del path non esiste
            return [] # O raise FileNotFoundError, ma [] è più sicuro per l'agente

        # Formattazione output (figli diretti)
        results = []
        children = current.get("children", {})
        
        for name, meta in children.items():
            results.append({
                "name": name,
                "type": meta["type"],
                "path": f"{path}/{name}".strip("/")
            })
            
        # Sort: Directory prima
        return sorted(results, key=lambda x: (x['type'] != 'dir', x['name']))

    def find_directories(self, snapshot_id: str, name_pattern: str, limit: int = 10) -> List[str]:
        """
        Ricerca ricorsiva nel Manifest (In-Memory).
        Molto più veloce che scaricare tutti i path e splittare stringhe.
        """
        manifest = self._get_manifest(snapshot_id)
        found = []
        pattern = name_pattern.lower()

        def _recurse(node, current_path):
            if len(found) >= limit: return

            children = node.get("children", {})
            for name, meta in children.items():
                full_path = f"{current_path}/{name}".strip("/")
                
                # Se è una directory
                if meta["type"] == "dir":
                    # Check match
                    if pattern in name.lower():
                        found.append(full_path)
                    # Continua a scendere
                    _recurse(meta, full_path)

        _recurse(manifest, "")
        return sorted(found)