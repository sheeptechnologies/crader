# Retrieval API

The retriever module handles search and context expansion.

## CodeRetriever

```python
from crader import CodeRetriever

retriever = CodeRetriever(storage, embedder)
```

### retrieve

```python
results = retriever.retrieve(
    query="Find auth middleware",
    repo_id=repo_id,
    snapshot_id=None,
    limit=10,
    strategy="hybrid",
    filters=None,
)
```

Arguments:

- `query`: natural language or code tokens.
- `repo_id`: repository ID used to resolve the active snapshot.
- `snapshot_id`: optional explicit snapshot override.
- `limit`: max results returned.
- `strategy`: `hybrid`, `vector`, or `keyword`.
- `filters`: dictionary of SQL filters (see `guides/retrieval-strategies.md`).

Returns a list of `RetrievedContext` objects.

## RetrievedContext

Key fields:

- `node_id`, `file_path`, `start_line`, `end_line`
- `content`
- `score`
- `semantic_labels`
- `retrieval_method`
- `parent_context` (string or None)
- `outgoing_definitions` (list of symbols)
- `nav_hints` (IDs for parent/prev/next)

### render

`render()` returns a Markdown-formatted string suitable for prompting.

## SearchExecutor

`SearchExecutor` exposes two static helpers used by the retriever:

- `vector_search(storage, embedder, query, limit, snapshot_id, filters, candidates)`
- `keyword_search(storage, query, limit, snapshot_id, filters, candidates)`

## GraphWalker

`GraphWalker` expands a node result by querying neighbors in the graph. It returns:

- `parent_context` describing the containing block
- `outgoing_definitions` listing called symbols
