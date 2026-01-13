# Retrieval strategies

`CodeRetriever` exposes a single entry point, `retrieve()`, which runs keyword, vector, or hybrid search against a snapshot.

## How retrieval works

1. **Snapshot resolution**
   - If `snapshot_id` is not provided, the active snapshot for the repository is resolved.

2. **Search execution**
   - Vector search uses `EmbeddingProvider.embed()` and `PostgresGraphStorage.search_vectors()`.
   - Keyword search uses `PostgresGraphStorage.search_fts()`.

3. **Fusion**
   - Hybrid search merges results with Reciprocal Rank Fusion (RRF).

4. **Context expansion**
   - `GraphWalker` adds parent context and outgoing definitions based on graph edges.

## Strategies

- `hybrid`: vector plus keyword, merged with RRF.
- `vector`: vector search only.
- `keyword`: keyword search only.

## Filters

The `filters` argument is pushed into SQL. Supported keys:

- `path_prefix`
- `language` / `exclude_language`
- `category` / `exclude_category`
- `role` / `exclude_role`

## Example

```python
from crader import CodeRetriever
from crader.providers.embedding import OpenAIEmbeddingProvider
from crader.storage.connector import PooledConnector
from crader.storage.postgres import PostgresGraphStorage

db_url = "postgresql://user:pass@localhost:5432/codebase"
connector = PooledConnector(dsn=db_url)
storage = PostgresGraphStorage(connector)
provider = OpenAIEmbeddingProvider(model="text-embedding-3-small")
retriever = CodeRetriever(storage, provider)

results = retriever.retrieve(
    query="authentication middleware",
    repo_id=repo_id,
    limit=5,
    strategy="hybrid",
    filters={"language": "python", "path_prefix": "src/"},
)
```
