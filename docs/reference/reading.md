# Reading API Reference

The `reader` module provides a **Virtual Filesystem** interface over the indexed snapshots. It allows agents to "mount" and browse a repository as if it were on a local disk, but with Time-Travel capabilities.

## CodeReader

```python
class CodeReader(storage: GraphStorage)
```

**Key Features:**

*   **Virtual FS**: Files are not stored as plain text on disk but as chunked nodes in Postgres. The Reader reconstructs them on-the-fly.
*   **O(1) Listings**: Directory contents are served from a pre-computed JSON manifest, making `ls` operations instant even for huge monorepos.
*   **Time Travel**: Every read operation is scoped to a `snapshot_id`, allowing you to read the filesystem exactly as it appeared in any past commit.

### Methods

#### `read_file`

```python
def read_file(self, snapshot_id: str, file_path: str, start_line: int = None, end_line: int = None) -> Dict[str, Any]
```

**Description:**
Reads file content. Supports partial reads (byte-range fetches) which is efficient for large files where you only need a specific function.

**Arguments:**

*   `snapshot_id` (str): The version to read from.
*   `file_path` (str): Relative path (e.g. `src/utils.py`).
*   `start_line` (int, optional): 1-indexed start line.
*   `end_line` (int, optional): 1-indexed end line.

**Returns:**
```python
{
    "file_path": "src/utils.py",
    "content": "def foo():\n    pass",
    "start_line": 10,
    "end_line": 11
}
```

---

#### `list_directory`

```python
def list_directory(self, snapshot_id: str, path: str = "") -> List[Dict[str, Any]]
```

**Description:**
Lists the children of a directory.

**Returns:**
A list of entries sorted with directories first.
```python
[
    {"name": "components", "type": "dir", "path": "src/components"},
    {"name": "App.js", "type": "file", "path": "src/App.js"}
]
```

---

#### `find_directories`

```python
def find_directories(self, snapshot_id: str, name_pattern: str, limit: int = 10) -> List[str]
```

**Description:**
Performs a "Fuzzy Find" for directory names. Useful when the agent knows a folder exists (e.g., "auth") but not where (e.g., `src/domain/auth` vs `libs/auth`).

**Implementation Note:**
This executes **In-Memory** against the cached JSON manifest. It does NOT impact the database.
