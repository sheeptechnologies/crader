import hashlib
import os
import re
from abc import ABC, abstractmethod
from typing import Any, Dict, List

from ..utils.git import GitClient
from ..utils.hashing import compute_file_hash


class MetadataProvider(ABC):
    """
    Abstract Inteface for Repository Metadata Extraction.

    Decouples the indexer from the underlying Version Control System (VCS).
    While `GitMetadataProvider` is the primary implementation, this abstraction allows
    indexing local folders, SVN repos, or purely synthetic codespaces.
    """

    @abstractmethod
    def get_repo_info(self) -> Dict[str, Any]:
        """Returns identity info: ID, Normalized URL, Commit Hash, Branch."""
        pass

    @abstractmethod
    def get_file_hash(self, file_path: str, content: bytes) -> str:
        """Computes a content-based hash (git-sha1 or sha256)."""
        pass

    @abstractmethod
    def get_file_category(self, file_path: str) -> str:
        """Classifies the file (code, test, config, docs) based on heuristics."""
        pass

    @abstractmethod
    def get_changed_files(self, since_commit: str) -> List[str]:
        """Returns a list of files changed between `since_commit` and HEAD."""
        pass


class GitMetadataProvider(MetadataProvider):
    """
    Standard Git-based Metadata Provider.

    Uses a `GitClient` (CLI wrapper) to extract authoritative info.

    **Key Features**:
    *   **Stable Identity**: Generates a consistent `repo_id` by hashing the normalized Remote URL.
        This ensures that two different clones of the same repo mapping to the same logical entity.
    *   **Local Fallback**: If no remote is configured (local project), hashes the absolute path.
    """

    def __init__(self, repo_path: str):
        self.repo_path = os.path.abspath(repo_path)
        self.git = GitClient(self.repo_path)
        self._info_cache = None

    def _sanitize_url(self, url: str) -> str:
        """
        Sanitizes the URL by removing credentials.
        """
        if not url:
            return ""
        pattern = r"(https?://|git@|ssh://)([^@]+@)(.+)"
        match = re.match(pattern, url)
        if match:
            protocol = match.group(1)
            rest = match.group(3)
            return f"{protocol}{rest}"
        return url

    def _normalize_repo_url(self, url: str) -> str:
        """
        Generates a canonical string for the ID generation.
        Strips protocols, auth, and .git extensions to ensure HTTPS and SSH URLs produce the same ID.
        """
        if not url:
            return ""
        clean = self._sanitize_url(url)
        clean = re.sub(r"^(https?://|git@|ssh://)", "", clean)
        if clean.endswith(".git"):
            clean = clean[:-4]
        clean = clean.replace(":", "/")
        return clean.strip("/")

    def get_repo_info(self) -> Dict[str, Any]:
        """
        Resolves the Repository Identity.

        Strategy:
        1.  Check `git remote get-url origin`.
        2.  If present, sanitize and hash it -> `repo_id`.
        3.  If absent (local-only), hash the absolute filesystem path -> `repo_id`.

        This guarantees that re-indexing the same folder always yields the same ID.
        """
        if self._info_cache:
            return self._info_cache

        # 1. Retrieve URL via existing Git client
        raw_url = self.git.get_remote_url()

        repo_id = ""
        sanitized_url = ""
        repo_name = "unknown"

        if raw_url:
            # CASE A: Repo with Remote (GitHub, GitLab, etc.)
            sanitized_url = self._sanitize_url(raw_url)
            normalized_id_source = self._normalize_repo_url(raw_url)
            # Hash of normalized URL for stable ID across forks/clones
            repo_id = hashlib.sha256(normalized_id_source.encode("utf-8")).hexdigest()

            if "/" in normalized_id_source:
                repo_name = normalized_id_source.split("/")[-1]
            else:
                repo_name = normalized_id_source
        else:
            # CASE B: Local Repo without Remote (pure git init)
            # [CRITICAL FIX] Use absolute path hash to guarantee:
            # 1. Uniqueness (two different folders have different IDs)
            # 2. Stability (the same folder always has the same ID, not random UUID)
            abs_path = os.path.abspath(self.repo_path)
            path_hash = hashlib.md5(abs_path.encode("utf-8")).hexdigest()

            repo_id = path_hash
            sanitized_url = f"local://{path_hash}"
            repo_name = os.path.basename(abs_path)

        # Retrieve the rest of info from Git
        info = {
            "repo_id": repo_id,
            "url": sanitized_url,
            "name": repo_name,
            "commit_hash": self.git.get_current_commit(),
            "branch": self.git.get_current_branch(),
            "local_path": self.repo_path,
        }

        self._info_cache = info
        return info

    def get_file_hash(self, file_path: str, content: bytes) -> str:
        return compute_file_hash(content)

    def get_file_category(self, file_path: str) -> str:
        lower = file_path.lower()
        if any(x in lower for x in ["test", "spec", "__tests__"]):
            return "test"
        if lower.endswith((".json", ".yaml", ".yml", ".env", ".toml", ".xml")):
            return "config"
        if lower.endswith((".md", ".txt", ".rst")):
            return "docs"
        return "code"

    def get_changed_files(self, since_commit: str) -> List[str]:
        return self.git.get_changed_files(since_commit)


class LocalMetadataProvider(MetadataProvider):
    """Fallback provider for non-git folders."""

    def __init__(self, repo_path: str):
        self.repo_path = os.path.abspath(repo_path)

    def get_repo_info(self) -> Dict[str, Any]:
        # Here too we use path hash for consistency
        abs_path = os.path.abspath(self.repo_path)
        repo_id = hashlib.sha256(abs_path.encode("utf-8")).hexdigest()

        return {
            "repo_id": repo_id,
            "url": f"local://{repo_id}",  # Standardize URL format
            "name": os.path.basename(abs_path),
            "commit_hash": "local",
            "branch": "main",
            "local_path": self.repo_path,
        }

    def get_file_hash(self, file_path: str, content: bytes) -> str:
        return compute_file_hash(content)

    def get_file_category(self, file_path: str) -> str:
        # Copy category logic or use shared helper if preferred
        lower = file_path.lower()
        if any(x in lower for x in ["test", "spec", "__tests__"]):
            return "test"
        if lower.endswith((".json", ".yaml", ".yml", ".env", ".toml", ".xml")):
            return "config"
        if lower.endswith((".md", ".txt", ".rst")):
            return "docs"
        return "code"

    def get_changed_files(self, since_commit: str) -> List[str]:
        return []
