import os
import uuid
import hashlib
import re
from abc import ABC, abstractmethod
from typing import Dict, List, Any
from ..utils.hashing import compute_file_hash
from ..utils.git import GitClient

class MetadataProvider(ABC):
    @abstractmethod
    def get_repo_info(self) -> Dict[str, Any]: pass
    @abstractmethod
    def get_file_hash(self, file_path: str, content: bytes) -> str: pass
    @abstractmethod
    def get_file_category(self, file_path: str) -> str: pass
    @abstractmethod
    def get_changed_files(self, since_commit: str) -> List[str]: pass

class GitMetadataProvider(MetadataProvider):
    def __init__(self, repo_path: str):
        self.repo_path = os.path.abspath(repo_path)
        self.git = GitClient(self.repo_path)
        self._info_cache = None

    def _sanitize_url(self, url: str) -> str:
        """
        Pulisce l'URL rimuovendo credenziali.
        """
        if not url: return ""
        pattern = r"(https?://|git@|ssh://)([^@]+@)(.+)"
        match = re.match(pattern, url)
        if match:
            protocol = match.group(1)
            rest = match.group(3)
            return f"{protocol}{rest}"
        return url

    def _normalize_repo_url(self, url: str) -> str:
        """
        Genera una stringa canonica per l'ID.
        """
        if not url: return ""
        clean = self._sanitize_url(url)
        clean = re.sub(r'^(https?://|git@|ssh://)', '', clean)
        if clean.endswith('.git'):
            clean = clean[:-4]
        clean = clean.replace(':', '/')
        return clean.strip('/')

    def get_repo_info(self) -> Dict[str, Any]:
        if self._info_cache: return self._info_cache
        
        # 1. Recuperiamo l'URL tramite il client Git esistente
        raw_url = self.git.get_remote_url()
        
        repo_id = ""
        sanitized_url = ""
        repo_name = "unknown"

        if raw_url:
            # CASO A: Repo con Remote (GitHub, GitLab, etc.)
            sanitized_url = self._sanitize_url(raw_url)
            normalized_id_source = self._normalize_repo_url(raw_url)
            # Hash dell'URL normalizzato per ID stabile tra fork/cloni
            repo_id = hashlib.sha256(normalized_id_source.encode('utf-8')).hexdigest()
            
            if '/' in normalized_id_source:
                repo_name = normalized_id_source.split('/')[-1]
            else:
                repo_name = normalized_id_source
        else:
            # CASO B: Repo Locale senza Remote (git init puro)
            # [FIX CRITICO] Usiamo l'hash del path assoluto per garantire:
            # 1. Unicità (due cartelle diverse hanno ID diversi)
            # 2. Stabilità (la stessa cartella ha sempre lo stesso ID, non random UUID)
            abs_path = os.path.abspath(self.repo_path)
            path_hash = hashlib.md5(abs_path.encode('utf-8')).hexdigest()
            
            repo_id = path_hash
            sanitized_url = f"local://{path_hash}"
            repo_name = os.path.basename(abs_path)

        # Recuperiamo il resto delle info da Git
        info = {
            "repo_id": repo_id,
            "url": sanitized_url,
            "name": repo_name,
            "commit_hash": self.git.get_current_commit(),
            "branch": self.git.get_current_branch(),
            "local_path": self.repo_path
        }
        
        self._info_cache = info
        return info

    def get_file_hash(self, file_path: str, content: bytes) -> str:
        return compute_file_hash(content)

    def get_file_category(self, file_path: str) -> str:
        lower = file_path.lower()
        if any(x in lower for x in ["test", "spec", "__tests__"]): return "test"
        if lower.endswith((".json", ".yaml", ".yml", ".env", ".toml", ".xml")): return "config"
        if lower.endswith((".md", ".txt", ".rst")): return "docs"
        return "code"

    def get_changed_files(self, since_commit: str) -> List[str]:
        return self.git.get_changed_files(since_commit)

class LocalMetadataProvider(MetadataProvider):
    """Provider fallback per cartelle non-git."""
    def __init__(self, repo_path: str):
        self.repo_path = os.path.abspath(repo_path)

    def get_repo_info(self) -> Dict[str, Any]:
        # Anche qui usiamo l'hash del path per coerenza
        abs_path = os.path.abspath(self.repo_path)
        repo_id = hashlib.sha256(abs_path.encode('utf-8')).hexdigest()
        
        return {
            "repo_id": repo_id,
            "url": f"local://{repo_id}", # Uniformiamo formato URL
            "name": os.path.basename(abs_path),
            "commit_hash": "local", 
            "branch": "main",
            "local_path": self.repo_path
        }

    def get_file_hash(self, file_path: str, content: bytes) -> str:
        return compute_file_hash(content)

    def get_file_category(self, file_path: str) -> str:
        # Copia logica categoria o usa helper condiviso se preferisci
        lower = file_path.lower()
        if any(x in lower for x in ["test", "spec", "__tests__"]): return "test"
        if lower.endswith((".json", ".yaml", ".yml", ".env", ".toml", ".xml")): return "config"
        if lower.endswith((".md", ".txt", ".rst")): return "docs"
        return "code"
        
    def get_changed_files(self, since_commit: str) -> List[str]: 
        return []