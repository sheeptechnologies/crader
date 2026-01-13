# Storage API Reference

The `storage` module is the foundation of the state management for the entire indexing system. It provides a robust, transactional abstraction over the persistence layer.

::: src.code_graph_indexer.storage.postgres

## Database Schema

The system is designed to run on **PostgreSQL 15+** with the **`pgvector`** extension (`v0.5+`).

### Key Concepts

*   **Repository Isolation**: All data is partitioned logically by `repository_id`. A single database cluster can host thousands of repositories.
*   **Snapshot Lifecycle**: Data is versioned using "Snapshots".
    *   `pending`: Data is being written (Indexing phase).
    *   `indexing`: Active processing (Embedding phase).
    *   `completed`: Immutable, ready for search.
    *   `failed`: Aborted run.

### Table Structure

#### `repositories`
| Column | Type | Description |
| :--- | :--- | :--- |
| `id` | UUID (PK) | Unique ID of the repository config. |
| `url` | TEXT | Git remote URL (e.g. `git@github.com...`). |
| `branch` | TEXT | Branch name (e.g. `main`). |
| `current_snapshot_id` | UUID (FK) | Pointer to the currently serving snapshot. **Critical for Readers.** |

#### `snapshots`
| Column | Type | Description |
| :--- | :--- | :--- |
| `id` | UUID (PK) | Unique ID of the indexing run. |
| `repository_id` | UUID (FK) | Parent repo. |
| `commit_hash` | CHAR(40) | Git SHA being indexed. |
| `status` | ENUM | State of the snapshot process. |
| `file_manifest` | JSONB | Compressed file listing for O(1) existence checks. |

#### `nodes` (The Graph Vertices)
| Column | Type | Description |
| :--- | :--- | :--- |
| `id` | UUID (PK) | Unique ID of the code chunk. |
| `snapshot_id` | UUID (FK) | Partition key. |
| `file_path` | TEXT | Path relative to repo root (e.g. `src/main.py`). |
| `chunk_hash` | CHAR(64) | SHA-256 of the content (for deduplication). |
| `content` | TEXT | The actual source code segment. |
| `metadata` | JSONB | Semantic tags (`role`, `category`, `language`). |



#### `nodes_fts` (Full-Text Search)
| Column | Type | Description |
| :--- | :--- | :--- |
| `node_id` | UUID (FK) | Link to `nodes`. |
| `file_path` | TEXT | Denormalized path for filtering. |
| `content` | TEXT | Raw content (Weight B). |
| `search_vector` | TSVECTOR | Weighted index: `A=tags`, `B=content`. |

#### `edges` (The Graph Relations)
| Column | Type | Description |
| :--- | :--- | :--- |
| `source_id` | UUID (FK) | Origin Node. |
| `target_id` | UUID (FK) | Destination Node. |
| `relation_type` | TEXT | Discriminator: `calls`, `defines`, `imports`, `inherits`, `child_of`. |

#### `node_embeddings` (Vectors)
| Column | Type | Description |
| :--- | :--- | :--- |
| `chunk_id` | UUID (FK) | Link to `nodes`. |
| `embedding` | VECTOR(1536) | The dense vector representation. |
| `vector_hash` | CHAR(64) | Identity hash for vector reuse optimization. |

---

## PostgresGraphStorage

```python
class PostgresGraphStorage(GraphStorage)
```

The concrete implementation of the storage interface. It manages connections, transactions, and optimized bulk operations.

### Initialization

#### `__init__`

```python
def __init__(self, connector: DatabaseConnector, vector_dim: int = 1536)
```
*   **connector**: An instance of `PooledConnector` or `SingleConnector` (for workers).
*   **vector_dim**: The dimension of the embeddings (default 1536 for OpenAI `text-embedding-3-small` / `ada-002`).

### Lifecycle Management

#### `ensure_repository`

```python
def ensure_repository(self, url: str, branch: str, name: str) -> str
```
**Idempotent**. Ensures a record exists for the given (URL, Branch) pair.
*   **Returns**: The `repository_id` (UUID).
*   **Concurrency**: Uses `ON CONFLICT DO UPDATE` to handle race conditions safely.

#### `create_snapshot`

```python
def create_snapshot(self, repository_id: str, commit_hash: str, force_new: bool = False) -> Tuple[Optional[str], bool]
```
Creates a new `pending` snapshot.
*   **force_new**: If `True`, creates a new snapshot even if one exists for this commit hash.
*   **Returns**: `(snapshot_id, created)`. If `created` is False, it means a reusable snapshot was found.

#### `activate_snapshot`

```python
def activate_snapshot(self, repository_id: str, snapshot_id: str, stats: Dict = None, manifest: Dict = None)
```
**Critical Operation**. Atomically promotes a snapshot to "Current".
1.  Updates `snapshots` table: Status -> `completed`, sets `completed_at`.
2.  Updates `repositories` table: `current_snapshot_id` -> `snapshot_id`.
3.  Because this happens in a single transaction, readers (Search) switch instantly to the new version with zero downtime.

### Write Operations (Optimized)

#### `add_nodes_raw`

```python
def add_nodes_raw(self, nodes_tuples: List[Tuple])
```
Bypasses ORM for speed.
*   **Input**: List of tuples matching the `nodes` table layout (excluding generated cols).
*   **Mechanism**: Uses `cursor.copy_expert()` with `COPY ... FROM STDIN (BINARY)`.
*   **Performance**: Capable of inserting 50k-100k rows/second.
*   **Raises**: `psycopg2.errors.UniqueViolation` if UUIDs collide (rare).

#### `ingest_scip_relations`

```python
def ingest_scip_relations(self, relations_tuples: List[Tuple], snapshot_id: str)
```
Performs a "Spatial Join" resolution. SCIP gives us `(path, start_line, end_line) -> Target`. We need to map this to `source_node_id -> target_node_id`.
1.  Loads raw data into `temp_scip_staging`.
2.  Executes a complex SQL `JOIN` between `temp_scip_staging` and `nodes` based on file path and byte ranges.
3.  Inserts resolved edges into `edges`.

### Read Operations (Search)

#### `search_vectors`

```python
def search_vectors(self, query_vector: List[float], limit: int, snapshot_id: str, filters: Dict[str, Any] = None) -> List[Dict]
```
Performs Approximate Nearest Neighbor (ANN) search.
*   **query_vector**: The float list from the embedding provider.
*   **snapshot_id**: **Mandatory**. Scopes search to the active version.
*   **filters**: Optional metadata filters. Supported keys:
    *   `language`: Exact match on file extension.
    *   `role`: Semantic role (e.g. `class`, `function`).
    *   `path_prefix`: Starts-with match on file path.
*   **SQL Strategy**:
    ```sql
    ORDER BY embedding <=> :query_vector LIMIT :limit
    ```
    This triggers the `HNSW` index scan.

#### `search_fts`

```python
def search_fts(self, query: str, limit: int, snapshot_id: str, filters: Dict = None) -> List[Dict]
```
Performs Full-Text Search.
*   Uses PostgreSQL's `websearch_to_tsquery` which supports Google-like syntax:
    *   `"exact phrase"`
    *   `login -test` (exclude word)
    *   `auth or security`
*   **Ranking**: Uses `ts_rank` for relevancy.

#### `get_file_manifest`

```python
def get_file_manifest(self, snapshot_id: str) -> Dict[str, Any]
```
Retrieves the cached directory tree structure.
*   **Use Case**: Used by the UI/Frontend to render the file explorer tree without querying the `files` table recursively.
*   **Return Format**: A nested dictionary representing the folder structure.
