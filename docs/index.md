# Crader

Crader turns Git repositories into a code property graph stored in PostgreSQL. It parses code into semantic chunks using Tree-sitter, stores structural relations, and supports keyword and vector search with snapshot isolation.

## What you can do

- Index a repository into immutable snapshots
- Generate embeddings with a staged, deduplicated pipeline
- Run hybrid search (vector plus keyword) with Reciprocal Rank Fusion
- Expand results with parent context and outgoing definitions
- Read files and navigate code blocks from the graph

## Key notes

- Semantic tagging via Tree-sitter queries is currently provided for Python, JavaScript, and TypeScript.
- The indexer always uses PostgreSQL; SQLite is intended for local experiments and tests. You can implement additional storage providers by extending the `GraphStorage` interface.

## Documentation

- Getting started: [Installation](getting-started/installation.md), [Quickstart](getting-started/quickstart.md)
- Guides: [Database schema](guides/schema/schema.md), [Indexing pipeline](guides/indexing-pipeline.md), [Embedding strategy](guides/embedding-strategy.md), [Retrieval strategies](guides/retrieval-strategies.md)
- API reference: [Indexer](reference/indexer.md), [Parsing](reference/parsing.md), [Embedding](reference/embedding.md), [Retrieval](reference/retrieval.md), [Storage](reference/storage.md), [Models](reference/models.md), [Reader](reference/reading.md), [Navigator](reference/navigation.md)
- Examples: [Advanced usage](examples/advanced_usage.md)
- Contributing: [Development setup](contributing/development-setup.md), [Code of conduct](contributing/code-of-conduct.md)
- Roadmap: [Roadmap](roadmap.md)
