# Parsing API

The parsing layer converts source files into chunked nodes and basic relations. It consists of a Tree-sitter parser and a SCIP indexer (currently the bottleneck for file-incremental indexing; see [Roadmap](../roadmap.md)).

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
- `List[CodeRelation]` (currently `child_of` relations within a file)

### Behavior

- Skips files larger than 1 MB, binaries, and minified or generated content.
- Uses Tree-sitter grammars based on file extension.
- Semantic tags are extracted from query files in `src/crader/parsing/queries/`.
  - Queries are provided for Python, JavaScript, and TypeScript.
- Chunk size limits are byte-based (`MAX_CHUNK_SIZE=800`, `CHUNK_TOLERANCE=400`).

## SCIPIndexer

`SCIPIndexer` runs external SCIP tools to extract cross-file relations such as calls and definitions.

```python
from crader.graph.indexers.scip import SCIPIndexer

indexer = SCIPIndexer(repo_path="/path/to/repo")
relations = list(indexer.stream_relations())
```

### Behavior

- Detects available indexers by project markers and file extensions.
- Builds a temporary SQLite symbol table to resolve references efficiently.
- Emits `CodeRelation` objects with byte ranges that are later resolved to node IDs in the database.
