# FAQ

## What is Crader?

Crader indexes Git repositories into a code property graph stored in PostgreSQL. It parses code into chunks using Tree-sitter, stores structural relationships, and supports keyword and vector search.

## Does Crader do incremental indexing?

Yes. Crader supports file-incremental indexing. When re-indexing a repository, only changed files are processed. Embeddings are deduplicated across snapshots by `vector_hash`.

## Which languages are supported?

Indexing scans files by extension:

- .py
- .js, .jsx
- .ts, .tsx
- .java
- .go
- .rs
- .c, .cpp
- .php
- .html, .css

Semantic tagging via Tree-sitter queries is currently provided for Python, JavaScript, and TypeScript.

## Why PostgreSQL instead of a separate vector database?

Crader stores the graph, full-text index, and vectors in one system. PostgreSQL provides transactional consistency and flexible joins between nodes, edges, and embeddings.

## `index()` returns "queued". What does it mean?

The repository is already locked by another indexing run (a snapshot is still marked `indexing`). Wait for it to finish or mark the stale snapshot as `failed` or `completed` in the database before retrying.

## Search returns empty or low-quality results

- Confirm that a snapshot is active for the repository.
- Run the embedding pipeline if you are using `strategy="vector"` or `strategy="hybrid"`.
- Remove restrictive filters like `path_prefix` or `language`.
- Use `strategy="keyword"` to validate FTS results independently of embeddings.

## Can I use local embeddings?

Yes. Use `FastEmbedProvider` for local embeddings or implement your own `EmbeddingProvider`.
