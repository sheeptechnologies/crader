import os
import logging
from typing import List, Dict, Optional, Union
from .storage.base import GraphStorage

logger = logging.getLogger(__name__)

class CodeReader:
    """
    Facade per l'accesso diretto al filesystem della repository.
    Permette di leggere file e listare directory, anche se non sono stati indicizzati
    (es. file minificati, config ignorati, ecc.).
    """
    
    def __init__(self, storage: GraphStorage):
        self.storage = storage

    def _resolve_physical_path(self, repo_id: str, relative_path: str = "") -> str:
        """
        Risolve il path fisico sicuro partendo dal repo_id.
        Lancia eccezione se il repo non esiste o se si tenta path traversal.
        """
        # 1. Recupera il path radice del worktree dal DB
        repo_record = self.storage.get_repository(repo_id)
        if not repo_record or not repo_record.get('local_path'):
            raise ValueError(f"Repository {repo_id} non trovata o path fisico non disponibile.")
            
        repo_root = os.path.abspath(repo_record['local_path'])
        
        # 2. Costruisce il path target
        # Puliamo il path relativo per evitare ./ o / all'inizio
        clean_rel_path = relative_path.lstrip(os.sep)
        target_path = os.path.abspath(os.path.join(repo_root, clean_rel_path))
        
        # 3. Security Check: Path Traversal Prevention
        # Il target deve iniziare con la repo_root
        if not target_path.startswith(repo_root):
            raise PermissionError(f"Accesso negato: {relative_path} è fuori dalla repository.")
            
        return target_path, repo_root

    def read_file(self, repo_id: str, file_path: str, start_line: int = None, end_line: int = None) -> str:
        """
        Legge il contenuto (o una parte) di un file fisico.
        """
        try:
            target_path, _ = self._resolve_physical_path(repo_id, file_path)
            
            if not os.path.isfile(target_path):
                return f"Error: File not found: {file_path}"

            # Lettura safe (gestione encoding e dimensione)
            # Limite di sicurezza: non leggiamo file > 10MB per non crashare la memoria
            file_size = os.path.getsize(target_path)
            if file_size > 10 * 1024 * 1024: 
                return f"Error: File too large ({file_size} bytes). Use index search for content."

            with open(target_path, 'r', encoding='utf-8', errors='replace') as f:
                # Se servono righe specifiche, leggiamo tutto e sliciamo
                # (Per file enormi si potrebbe usare `itertools.islice`, ma per codice sorgente va bene così)
                if start_line is not None or end_line is not None:
                    lines = f.readlines()
                    start = max(0, start_line - 1) if start_line else 0
                    end = end_line if end_line else len(lines)
                    
                    if start >= len(lines):
                        return "" # Range fuori limite
                        
                    content = "".join(lines[start:end])
                    # Aggiungiamo contesto visivo per l'LLM
                    return f"--- File: {file_path} (Lines {start+1}-{end}) ---\n{content}"
                else:
                    return f.read()

        except Exception as e:
            logger.error(f"Read error {file_path}: {e}")
            return f"Error reading file: {str(e)}"

    def list_directory(self, repo_id: str, path: str = "") -> str:
        """
        Elenca file e cartelle. Utile per l'esplorazione ('ls').
        Non ricorsivo per default per non inondare il contesto.
        """
        try:
            target_path, repo_root = self._resolve_physical_path(repo_id, path)
            
            if not os.path.isdir(target_path):
                return f"Error: Not a directory: {path}"

            items = []
            try:
                # Ordina: Cartelle prima, poi file
                with os.scandir(target_path) as it:
                    entries = sorted(list(it), key=lambda e: (not e.is_dir(), e.name.lower()))
                    
                    for entry in entries:
                        # Ignoriamo solo la cartella .git per pulizia, ma mostriamo il resto
                        if entry.name == ".git": continue
                        
                        prefix = "📁 " if entry.is_dir() else "📄 "
                        # Calcoliamo path relativo da mostrare all'agente
                        rel_entry = os.path.relpath(entry.path, repo_root)
                        items.append(f"{prefix}{entry.name}")
                        
            except PermissionError:
                return "Error: Permission denied scanning directory."

            return f"Directory listing for '{path or '/'}':\n" + "\n".join(items)

        except Exception as e:
            return f"Error listing directory: {str(e)}"