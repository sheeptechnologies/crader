# Reading API

`CodeReader` provides file and directory access on top of snapshot data.

## CodeReader

```python
from crader import CodeReader

reader = CodeReader(storage)
```

### read_file

```python
data = reader.read_file(snapshot_id, "src/app.py", start_line=1, end_line=80)
```

Returns a dictionary with `file_path`, `content`, `start_line`, and `end_line`. Reads are line-based and reconstructed from stored chunks.

If a file exists but has no stored chunks (for example, it was skipped), the content may be an empty string.

### list_directory

```python
entries = reader.list_directory(snapshot_id, "src")
```

Uses the snapshot manifest and returns a list of `{name, type, path}` entries.

### find_directories

```python
paths = reader.find_directories(snapshot_id, "tests", limit=10)
```

Performs an in-memory search on the manifest and returns matching directory paths.
