# API Reference

## `code_graph_indexer.CodebaseIndexer`

The main entry point for the library.

### Initialization

```python
indexer = CodebaseIndexer(repo_path: str)
```

-   **`repo_path`** *(str)*: The absolute or relative path to the repository you want to index.

### Methods

#### `index()`

```python
def index(self) -> None
```

Triggers the full indexing process:
1.  Parses files using Tree-sitter.
2.  Runs SCIP indexing.
3.  Builds the Knowledge Graph.
4.  Commits data to the storage.

#### `get_stats()`

```python
def get_stats(self) -> Dict[str, Any]
```

Returns statistics about the indexed graph, such as the count of nodes, edges, and files.

#### `get_nodes()`

```python
def get_nodes(self) -> List[Node]
```

Retrieves all nodes from the graph.

#### `get_edges()`

```python
def get_edges(self) -> List[Edge]
```

Retrieves all edges from the graph.

#### `close()`

```python
def close(self) -> None
```

Closes the connection to the underlying storage. It is recommended to call this when done, or let the garbage collector handle it via `__del__`.
