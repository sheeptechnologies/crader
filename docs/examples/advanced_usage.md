# Advanced usage

This page collects practical patterns that match the current API surface.

Assume you already created a PostgreSQL storage instance and resolved `repo_id` and `snapshot_id` as needed.

## Index multiple branches

Each `(url, branch)` pair is a separate repository record.

```python
from crader import CodebaseIndexer

indexer_main = CodebaseIndexer(
    repo_url="git@github.com:org/repo.git",
    branch="main",
    db_url=db_url,
)
indexer_main.index()
indexer_main.close()

indexer_dev = CodebaseIndexer(
    repo_url="git@github.com:org/repo.git",
    branch="feature/new-search-api",
    db_url=db_url,
)
indexer_dev.index(force=True)
indexer_dev.close()
```

## Keyword-only search

Keyword search does not require embeddings, but `CodeRetriever` still needs a provider instance.

```python
from crader import CodeRetriever
from crader.providers.embedding import DummyEmbeddingProvider

retriever = CodeRetriever(storage, DummyEmbeddingProvider())
results = retriever.retrieve(
    query="AuthMiddleware",
    repo_id=repo_id,
    strategy="keyword",
)
```

## Use filters to reduce noise

```python
results = retriever.retrieve(
    query="router",
    repo_id=repo_id,
    strategy="hybrid",
    filters={
        "language": ["python"],
        "exclude_category": ["test"],
        "path_prefix": ["src/"]
    },
)
```

## Read files and navigate chunks

```python
from crader import CodeReader, CodeNavigator

reader = CodeReader(storage)
nav = CodeNavigator(storage)

# List a directory
entries = reader.list_directory(snapshot_id, "src")

# Read a file range
file_data = reader.read_file(snapshot_id, "src/app.py", start_line=1, end_line=80)

# Move to the next chunk in a file
next_chunk = nav.read_neighbor_chunk(node_id, direction="next")
```

## Control the repo storage location

```bash
export CRADER_REPO_VOLUME="/mnt/crader/repos"
```

This affects the bare mirrors and worktrees created by `GitVolumeManager`.

## Embed without external APIs

Use `mock_api=True` to generate random vectors for testing:

```python
async for update in indexer.embed(provider, batch_size=200, mock_api=True):
    if update.get("status") == "completed":
        print(update)
```
