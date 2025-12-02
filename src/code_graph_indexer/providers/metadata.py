import os
import uuid
import hashlib
import re
from abc import ABC, abstractmethod
from typing import Dict, List
from ..utils.hashing import compute_file_hash
from ..utils.git import GitClient

class MetadataProvider(ABC):
    @abstractmethod
    def get_repo_info(self) -> Dict[str, str]: pass
    @abstractmethod
    def get_file_hash(self, file_path: str, content: bytes) -> str: pass
    @abstractmethod
    def get_file_category(self, file_path: str) -> str: pass
    @abstractmethod
    def get_changed_files(self, since_commit: str) -> List[str]: pass

class GitMetadataProvider(MetadataProvider):
    def __init__(self, repo_path: str):
        self.repo_path = repo_path
        self.git = GitClient(repo_path)
        self._info_cache = None

    def _sanitize_url(self, url: str) -> str:
        """
        Pulisce l'URL rimuovendo credenziali e username per la privacy.
        Es: 'https://user:token@github.com/org/repo.git' -> 'https://github.com/org/repo.git'
        Es: 'https://filippo%20daminato@github.com/...' -> 'https://github.com/...'
        """
        if not url: return ""
        
        # Regex per catturare "user:pass@" o "user@" prima dell'host
        # Supporta http, https, ssh, git
        pattern = r"(https?://|git@|ssh://)([^@]+@)(.+)"
        match = re.match(pattern, url)
        if match:
            protocol = match.group(1)
            # group(2) è la parte 'user:pass@', la scartiamo
            rest = match.group(3)
            return f"{protocol}{rest}"
            
        return url

    def _normalize_repo_url(self, url: str) -> str:
        """
        Genera una stringa canonica per l'ID (senza protocollo, estensione, ecc).
        """
        if not url: return f"local-{uuid.uuid4()}"
        
        # 1. Sanitizza prima (rimuovi user@)
        clean = self._sanitize_url(url)
        
        # 2. Rimuovi protocollo
        clean = re.sub(r'^(https?://|git@|ssh://)', '', clean)
        
        # 3. Rimuovi estensione .git
        if clean.endswith('.git'):
            clean = clean[:-4]
            
        # 4. Sostituisci : di SSH con /
        clean = clean.replace(':', '/')
            
        return clean.strip('/')

    def get_repo_info(self) -> Dict[str, str]:
        if self._info_cache: return self._info_cache
        
        raw_url = self.git.get_remote_url()
        
        # URL pulito per lo storage (privacy)
        sanitized_url = self._sanitize_url(raw_url)
        
        # ID normalizzato (univocità)
        normalized_id_source = self._normalize_repo_url(raw_url)
        repo_id = hashlib.sha256(normalized_id_source.encode('utf-8')).hexdigest()
        
        repo_name = normalized_id_source.split('/')[-1] if '/' in normalized_id_source else "unknown-repo"

        info = {
            "repo_id": repo_id,
            "url": sanitized_url or "local", # Salviamo l'URL pulito!
            "name": repo_name,
            "commit_hash": self.git.get_current_commit(),
            "branch": self.git.get_current_branch()
        }
        self._info_cache = info
        return info

    def get_file_hash(self, file_path: str, content: bytes) -> str:
        return compute_file_hash(content)

    def get_file_category(self, file_path: str) -> str:
        lower = file_path.lower()
        if any(x in lower for x in ["test", "spec", "__tests__"]): return "test"
        if lower.endswith((".json", ".yaml", ".yml", ".env", ".toml", ".xml")): return "config"
        if any(x in lower for x in ["config"]): return "config"
        if lower.endswith((".md", ".txt", ".rst")): return "docs"
        return "code"

    def get_changed_files(self, since_commit: str) -> List[str]:
        return self.git.get_changed_files(since_commit)

class LocalMetadataProvider(GitMetadataProvider):
    def get_repo_info(self) -> Dict[str, str]:
        abs_path = os.path.abspath(self.repo_path)
        repo_id = hashlib.sha256(abs_path.encode('utf-8')).hexdigest()
        return {
            "repo_id": repo_id,
            "url": abs_path,
            "name": os.path.basename(abs_path),
            "commit_hash": "local", 
            "branch": "main"
        }
    def get_changed_files(self, since_commit: str) -> List[str]: return []