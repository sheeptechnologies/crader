# Indexer API Reference

The `indexer` module is the command center of the library. It acts as the coordinator between the Git filesystem, the Parsing workers, the Embedding provider, and the Storage layer.

::: src.code_graph_indexer.indexer

## CodebaseIndexer

```python
class CodebaseIndexer(repo_url: str, branch: str, db_url: Optional[str] = None, worker_telemetry_init: Optional[Callable] = None)
```

The persistent controller for a specific repository.

### Constructor Arguments

*   **`repo_url`** *(str)*:
    The complete Git remote URL. Supports SSH (`git@...`) and HTTPS (`https://...`). This acts as part of the unique key for the repository.
    
*   **`branch`** *(str)*:
    The target branch to index (e.g., `main`, `master`, `develop`).
    
*   **`db_url`** *(Optional[str])*:
    A standard PostgreSQL connection string (`postgresql://server:port/db`). If not provided, it looks for `DATABASE_URL` in environment variables.
    
*   **`worker_telemetry_init`** *(Optional[Callable])*:
    A hook function called at the start of every worker process. Useful for initializing tools like Sentry, OpenTelemetry, or custom logging config in parallel processes.

---

### Methods

#### `index`

```python
def index(self, force: bool = False, auto_prune: bool = False) -> str
```

**Description:**
Triggers the "Structure Analysis" phase. This involves cloning, parsing, and graph construction. This is a synchronous, blocking operation (though it uses internal parallelism).

**Arguments:**
*   `force` (bool): If `True`, ignores the "Stale Check". Even if the latest commit is already indexed, it will create a new Snapshot and re-parse everything. Useful for development or fixing corrupted indices.
*   `auto_prune` (bool): If `True`, automatically sets old snapshots to `archived` or deletes them after successful indexing.

**Returns:**
*   `snapshot_id` (str): The UUID of the newly created (or existing reused) snapshot.

**Raises:**
*   `GitCommandError`: If cloning or fetching fails (e.g. auth error, network down).
*   `DatabaseError`: If connection fails.

---

#### `embed`

```python
async def embed(self, provider: EmbeddingProvider, batch_size: int = 1000, mock_api: bool = False, force_snapshot_id: str = None) -> AsyncGenerator[Dict[str, Any], None]
```

**Description:**
Triggers the "Semantic Analysis" phase. This is an **Asynchronous Generator**. You must iterate over it to drive the process forward. It is decoupled from `index()` to allow for different scheduling (e.g., Index now, Embed tonight).

**Arguments:**
*   `provider` (EmbeddingProvider): An instance of a provider wrapper (e.g. `OpenAIEmbeddingProvider`).
*   `batch_size` (int): Number of vectors to flush to DB in one transaction. Default 1000.
*   `mock_api` (bool): If `True`, generates random vectors. STRICTLY FOR TESTING.
*   `force_snapshot_id` (str): target a specific historical snapshot instead of the current one.

**Yields:**
A stream of status dictionaries.
*   `{'status': 'staging_progress', 'total': 100, 'staged': 50}`
*   `{'status': 'embedding_progress', 'total_to_embed': 500, 'total_embedded': 100}`
*   `{'status': 'completed', 'newly_embedded': 450, 'recovered_from_history': 50}`

---

#### `_init_worker_process` (Internal)

```python
def _init_worker_process(worktree_path: str, db_url: str)
```

**Global State Initialization**:
This static method is the entry point for every `multiprocessing.Process` spawned by the indexer.
1.  disables Signals to avoid `KeyboardInterrupt` corruption.
2.  Initializes a thread-local `TreeSitterRepoParser`.
3.  Opens a **new** DB connection (as connections strictly cannot be shared across forks).

---

## Utility: Pruning

### `prune_snapshots`

```python
def prune_snapshots(self, keep: int = 3)
```

Maintenance method to keep the database size in check.
*   Keeps the last `keep` (default 3) snapshots for this repository.
*   Hard deletes older snapshots and cascades deletes to `nodes`, `edges`, and `embeddings`.
*   **Warning**: This is destructive and irreversible.
