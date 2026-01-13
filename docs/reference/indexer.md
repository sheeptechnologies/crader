# Indexer API

`CodebaseIndexer` orchestrates repository indexing and embedding. It uses PostgreSQL for storage and the Git volume manager for repository worktrees.

## CodebaseIndexer

```python
from crader import CodebaseIndexer

indexer = CodebaseIndexer(
    repo_url="https://github.com/org/repo.git",
    branch="main",
    db_url="postgresql://user:pass@localhost:5432/codebase",
)
```

### Constructor arguments

- `repo_url` (str): Git remote URL or any URL accepted by `git clone`.
- `branch` (str): Branch or tag to index.
- `db_url` (str, optional): PostgreSQL DSN. Falls back to `CRADER_DB_URL`.
- `worker_telemetry_init` (callable, optional): Hook executed in worker processes.

### index

```python
snapshot_id = indexer.index(force=False, auto_prune=False)
```

Runs parsing and relation extraction and stores results in the database.

- `force`: if `True`, create a new snapshot even if the commit is already indexed.
- `auto_prune`: currently a placeholder. Old snapshots are not removed automatically.

Returns the snapshot ID, or the string `"queued"` when another indexing run is in progress.

### embed

```python
async for update in indexer.embed(provider, batch_size=1000, mock_api=False, force_snapshot_id=None):
    ...
```

Runs the asynchronous embedding pipeline. This does not run automatically during `index()`.

- `provider`: an `EmbeddingProvider` implementation.
- `batch_size`: staging batch size.
- `mock_api`: generate random vectors for testing.
- `force_snapshot_id`: embed a specific snapshot instead of the active one.

### get_stats

```python
stats = indexer.get_stats()
```

Returns counters from the database (`files`, `total_nodes`, `embeddings`, `snapshots`, `repos`).

### close

```python
indexer.close()
```

Closes the database connection pool.
