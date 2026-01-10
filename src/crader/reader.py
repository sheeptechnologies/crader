import logging
from typing import Any, Dict, List

from .storage.base import GraphStorage

logger = logging.getLogger(__name__)


class CodeReader:
    """
    Virtual Filesystem Reader based on Immutable Snapshots.

    This facade provides high-performance, consistent read access to the repository's file structure and content
    as it existed at a specific point in time (Snapshot). It abstracts the underlying storage mechanism (which stores files as distributed chunks)
    into a coherent file system interface.

    **Key Architectures:**
    *   **Manifest-Based Listing**: Directory listings are O(1) operations served from a pre-computed JSON manifest stored with the snapshot, creating a "Virtual File System".
    *   **Lazy Loading**: File contents are re-assembled on-demand from database chunks, ensuring that reading small sections of large files is efficient.
    *   **Caching**: Implements session-level caching for manifests to minimize database round-trips during heavy read sessions.

    Attributes:
        storage (GraphStorage): The storage backend interface.
        _manifest_cache (Dict): Internal cache to store loaded snapshot manifests.
    """

    def __init__(self, storage: GraphStorage):
        self.storage = storage
        # Local manifest cache per session (optional, but useful if the Reader object is long-lived)
        self._manifest_cache = {}

    def _get_manifest(self, snapshot_id: str) -> Dict:
        if snapshot_id not in self._manifest_cache:
            self._manifest_cache[snapshot_id] = self.storage.get_snapshot_manifest(snapshot_id)
        return self._manifest_cache[snapshot_id]

    def read_file(
        self, snapshot_id: str, file_path: str, start_line: int = None, end_line: int = None
    ) -> Dict[str, Any]:
        """
        Retrieves file content, potentially partial, from the snapshot.

        This method reconstructs the file (or a segment of it) by querying the underlying chunks in the database.
        It is optimized to fetch only the data strictly necessary for the requested range.

        Args:
            snapshot_id (str): The ID of the snapshot to read from.
            file_path (str): The relative path of the file (e.g., 'src/main.py').
            start_line (Optional[int]): The 1-based start line number (inclusive). Defaults to beginning of file.
            end_line (Optional[int]): The 1-based end line number (inclusive). Defaults to end of file.

        Returns:
            Dict[str, Any]: A dictionary containing:
                *   'file_path': The input path.
                *   'content': The string content of the file (or range).
                *   'start_line': The actual start line returned.
                *   'end_line': The actual end line returned.

        Raises:
            FileNotFoundError: If the file path does not exist in the specified snapshot.
        """
        # Note: We could also validate file existence by looking at the manifest before calling the DB
        content = self.storage.get_file_content_range(snapshot_id, file_path, start_line, end_line)

        if content is None:
            raise FileNotFoundError(f"File '{file_path}' not found in snapshot {snapshot_id[:8]}")

        return {
            "file_path": file_path,
            "content": content,
            "start_line": start_line or 1,
            "end_line": end_line or "EOF",
        }

    def list_directory(self, snapshot_id: str, path: str = "") -> List[Dict[str, Any]]:
        """
        Lists the contents of a directory using the O(1) Manifest mechanism.

        This method traverses the pre-loaded JSON manifest tree to find the target directory and lists its immediate children.
        It does NOT query the database files table directly, making it extremely fast even for huge repositories.

        Args:
            snapshot_id (str): The ID of the snapshot.
            path (str): The relative path to the directory to list (empty string for root).

        Returns:
            List[Dict[str, Any]]: A sorted list of directory entries, where each entry is:
                *   'name': Name of the file/dir.
                *   'type': 'file' or 'dir'.
                *   'path': Full relative path.

            Result is sorted so directories appear first.
        """
        manifest = self._get_manifest(snapshot_id)

        # Navigation in the JSON tree
        current = manifest
        # Remove leading/trailing slashes
        target_parts = [p for p in path.split("/") if p]

        try:
            # Descend into tree
            for part in target_parts:
                current = current["children"][part]
                if current["type"] != "dir":
                    raise NotADirectoryError(f"{path} is not a directory.")
        except KeyError:
            # If a part of the path does not exist
            return []  # Or raise FileNotFoundError, but [] is safer for the agent

        # Output formatting (direct children)
        results = []
        children = current.get("children", {})

        for name, meta in children.items():
            results.append({"name": name, "type": meta["type"], "path": f"{path}/{name}".strip("/")})

        # Sort: Directory first
        return sorted(results, key=lambda x: (x["type"] != "dir", x["name"]))

    def find_directories(self, snapshot_id: str, name_pattern: str, limit: int = 10) -> List[str]:
        """
        Performs a recursive 'fuzzy find' for directories within the Manifest.

        This execution happens entirely In-Memory on the JSON structure, avoiding expensive database `LIKE` queries over paths.
        Useful for fuzzy navigation like "find the 'tests' folder".

        Args:
            snapshot_id (str): The ID of the snapshot.
            name_pattern (str): The substring to search for in directory names (e.g., "utils").
            limit (int): Max number of results to return.

        Returns:
            List[str]: A list of matching directory paths.
        """
        manifest = self._get_manifest(snapshot_id)
        found = []
        pattern = name_pattern.lower()

        def _recurse(node, current_path):
            if len(found) >= limit:
                return

            children = node.get("children", {})
            for name, meta in children.items():
                full_path = f"{current_path}/{name}".strip("/")

                # If it is a directory
                if meta["type"] == "dir":
                    # Check match
                    if pattern in name.lower():
                        found.append(full_path)
                    # Continua a scendere
                    _recurse(meta, full_path)

        _recurse(manifest, "")
        return sorted(found)
