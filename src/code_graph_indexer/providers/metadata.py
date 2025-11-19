import os
import uuid
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

    def get_repo_info(self) -> Dict[str, str]:
        if self._info_cache: return self._info_cache
        info = {
            "repo_id": self.git.get_remote_url() or f"repo-{uuid.uuid4()}",
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
        if lower.endswith((".json", ".yaml", ".yml", ".env")): return "config"
        return "code"

    def get_changed_files(self, since_commit: str) -> List[str]:
        return self.git.get_changed_files(since_commit)

class LocalMetadataProvider(GitMetadataProvider):
    def get_repo_info(self) -> Dict[str, str]:
        return {"repo_id": f"local-{os.path.basename(self.repo_path)}", "commit_hash": "local", "branch": "main"}
    def get_changed_files(self, since_commit: str) -> List[str]: return []