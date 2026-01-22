# Parsing API

The parsing layer converts source files into chunked nodes and structural relations using Tree-sitter.

## TreeSitterRepoParser

```python
from crader.parsing.parser import TreeSitterRepoParser

parser = TreeSitterRepoParser(repo_path="/path/to/repo")
parser.snapshot_id = "<snapshot-id>"
```

### stream_semantic_chunks

```python
for file_rec, nodes, contents, relations in parser.stream_semantic_chunks(file_list=["src/app.py"]):
    ...
```

Yields:

- `FileRecord`
- `List[ChunkNode]`
- `List[ChunkContent]`
- `List[CodeRelation]` (`child_of` relations within a file)

### Behavior

- Skips files larger than 1 MB, binaries, and minified or generated content.
- Uses Tree-sitter grammars based on file extension.
- Semantic tags are extracted from query files in `src/crader/parsing/queries/`.
  - Queries are provided for Python, JavaScript, and TypeScript.
- Chunk size limits are byte-based (`MAX_CHUNK_SIZE=800`, `CHUNK_TOLERANCE=400`).
