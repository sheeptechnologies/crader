import os
import logging
from typing import List, Dict, Optional, Any
from .storage.base import GraphStorage

logger = logging.getLogger(__name__)

class CodeReader:
    """
    Facade per l'accesso diretto al filesystem della repository.
    Restituisce dati strutturati (Dict/List) per consumo programmatico.
    """
    
    def __init__(self, storage: GraphStorage):
        self.storage = storage

    def _resolve_physical_path(self, repo_id: str, relative_path: str = "") -> str:
        """
        Risolve il path fisico sicuro partendo dal repo_id.
        Lancia eccezione se il repo non esiste o se si tenta path traversal.
        """
        repo_record = self.storage.get_repository(repo_id)
        if not repo_record or not repo_record.get('local_path'):
            raise ValueError(f"Repository {repo_id} non trovata o path fisico non disponibile.")
            
        repo_root = os.path.abspath(repo_record['local_path'])
        clean_rel_path = relative_path.lstrip(os.sep)
        target_path = os.path.abspath(os.path.join(repo_root, clean_rel_path))
        
        if not target_path.startswith(repo_root):
            raise PermissionError(f"Accesso negato: {relative_path} è fuori dalla repository.")
            
        return target_path, repo_root

    def read_file(self, repo_id: str, file_path: str, start_line: int = None, end_line: int = None) -> Dict[str, Any]:
        """
        Legge il contenuto di un file fisico.
        Returns:
            Dict con keys: 'file_path', 'content', 'start_line', 'end_line', 'size_bytes'.
        """
        target_path, _ = self._resolve_physical_path(repo_id, file_path)
        
        if not os.path.isfile(target_path):
            raise FileNotFoundError(f"File non trovato: {file_path}")

        file_size = os.path.getsize(target_path)
        if file_size > 10 * 1024 * 1024: 
            raise ValueError(f"File troppo grande ({file_size} bytes).")

        try:
            with open(target_path, 'r', encoding='utf-8', errors='replace') as f:
                content = ""
                actual_start = 0
                actual_end = 0
                
                if start_line is not None or end_line is not None:
                    lines = f.readlines()
                    actual_start = max(0, start_line - 1) if start_line else 0
                    actual_end = end_line if end_line else len(lines)
                    
                    if actual_start < len(lines):
                        content = "".join(lines[actual_start:actual_end])
                else:
                    content = f.read()
                    actual_end = content.count('\n') + 1

                return {
                    "file_path": file_path,
                    "content": content,
                    "start_line": actual_start + 1,
                    "end_line": actual_end,
                    "size_bytes": len(content.encode('utf-8'))
                }

        except Exception as e:
            logger.error(f"Read error {file_path}: {e}")
            raise IOError(f"Errore lettura file: {str(e)}")
        
    def find_directories(self, repo_id: str, name_pattern: str, limit: int = 10) -> List[str]:
        """
        Cerca cartelle che contengono 'name_pattern' nel nome.
        Utile quando l'agente non sa il path esatto (es. 'flask' è in 'src/flask'?).
        """
        _, repo_root = self._resolve_physical_path(repo_id, "")
        
        matches = []
        term = name_pattern.lower()
        
        # Walk veloce
        for root, dirs, _ in os.walk(repo_root):
            # Rimuoviamo cartelle ignorate per velocità
            dirs[:] = [d for d in dirs if d not in {'.git', '__pycache__', 'node_modules', 'venv', '.venv'}]
            
            for d in dirs:
                if term in d.lower():
                    rel_path = os.path.relpath(os.path.join(root, d), repo_root)
                    matches.append(rel_path)
                    if len(matches) >= limit:
                        return matches
        
        return matches

    def list_directory(self, repo_id: str, path: str = "") -> List[Dict[str, Any]]:
        """
        Elenca file e cartelle.
        Returns:
            List[Dict] con keys: 'name', 'type' ('file'|'dir'), 'path'.
        """
        target_path, repo_root = self._resolve_physical_path(repo_id, path)
        
        if not os.path.isdir(target_path):
            raise NotADirectoryError(f"Non è una directory: {path}")

        results = []
        try:
            with os.scandir(target_path) as it:
                # Ordiniamo per tipo (dir prima) e poi nome
                entries = sorted(list(it), key=lambda e: (not e.is_dir(), e.name.lower()))
                
                for entry in entries:
                    if entry.name == ".git": continue
                    
                    entry_type = "dir" if entry.is_dir() else "file"
                    rel_path = os.path.relpath(entry.path, repo_root)
                    
                    results.append({
                        "name": entry.name,
                        "type": entry_type,
                        "path": rel_path
                    })
        except PermissionError:
            raise PermissionError(f"Permesso negato per: {path}")

        return results