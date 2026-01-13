# Storage API

Crader uses PostgreSQL as the primary backend. The indexer writes directly through `PostgresGraphStorage`.

## PostgresGraphStorage

```python
from crader.storage.connector import PooledConnector
from crader.storage.postgres import PostgresGraphStorage

connector = PooledConnector(dsn=db_url)
storage = PostgresGraphStorage(connector)
```

### Repository and snapshot lifecycle

- `ensure_repository(url, branch, name) -> repo_id`
- `create_snapshot(repository_id, commit_hash, force_new=False) -> (snapshot_id, is_new)`
  - returns `(None, False)` if another snapshot is currently `indexing`
- `activate_snapshot(repository_id, snapshot_id, stats=None, manifest=None)`
- `get_active_snapshot_id(repository_id) -> snapshot_id`
- `fail_snapshot(snapshot_id, error)`

### Write operations

- `add_files`, `add_nodes`, `add_contents`
- `add_search_index` builds the FTS table
- `add_edge` and `ingest_scip_relations` add relations
- Raw bulk variants exist for high-throughput inserts (`add_nodes_raw`, etc.)

### Search

- `search_vectors(query_vector, limit, snapshot_id, filters=None)`
- `search_fts(query, limit, snapshot_id, filters=None)`

Filter keys:

- `path_prefix`
- `language` / `exclude_language`
- `category` / `exclude_category`
- `role` / `exclude_role`

### Navigation helpers

- `get_context_neighbors(node_id)`
- `get_neighbor_chunk(node_id, direction)`
- `get_neighbor_metadata(node_id)`
- `get_incoming_references(node_id)`
- `get_outgoing_calls(node_id)`

### Embedding pipeline helpers

- `prepare_embedding_staging()`
- `load_staging_data()`
- `backfill_staging_vectors(snapshot_id)`
- `fetch_staging_delta(snapshot_id)`
- `flush_staged_hits(snapshot_id)`
- `save_embeddings_direct(records)`

## SQLite backend

`SqliteGraphStorage` exists for local experiments and tests. The main indexer uses PostgreSQL and does not switch backends automatically.
