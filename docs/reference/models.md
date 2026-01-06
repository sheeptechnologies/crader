# Data Models

The `models` module defines the data transfer objects (DTOs) used throughout the system. These reflect the database schema.

::: src.code_graph_indexer.models

## Core Entities

### `Repository`

```python
@dataclass
class Repository
```

The persistent identity of a project.
*   **current_snapshot_id**: Pointer to the "LIVE" version.
*   **reindex_requested_at**: Flag used for distributed locking/coordination.

### `Snapshot`

```python
@dataclass
class Snapshot
```

An immutable point-in-time capture of the repository.
*   **status**: `pending` -> `indexing` -> `completed`.
*   **file_manifest**: A JSON tree structure caching the file list for O(1) directory navigation.

### `ChunkNode`

```python
@dataclass
class ChunkNode
```

The atom of the Knowledge Graph.
*   **chunk_hash**: SHA-256 of the content (used for deduplication).
*   **byte_range**: `[start_byte, end_byte]` for precise slicing.
*   **metadata**: Flexible JSON store for semantic tags (`role`, `category`).

### `CodeRelation`

```python
@dataclass
class CodeRelation
```

A directed edge between nodes (or files).
*   **relation_type**:
    *   `child_of`: Structural hierarchy.
    *   `calls ` / `references`: Usage.
    *   `inherits`: OOP Inheritance.
    *   `imports`: Module Dependency.

## Retrieval Objects

### `RetrievedContext`

```python
@dataclass
class RetrievedContext
```

The rich object returned to the client/agent after a search.
*   **nav_hints**: Pre-calculated IDs for "Next Chunk", "Previous Chunk", and "Parent Container" to enable UI navigation without extra queries.
*   **outgoing_definitions**: List of symbols called by this chunk (e.g. `User.save()`) to provide immediate context to an LLM.

#### `render`

```python
def render(self) -> str
```

Formats the context into a Markdown-friendly string optimized for LLM consumption.
Includes specific headers `[CONTEXT]`, `[CODE]`, `[RELATIONS]` to help the model parse the input.
