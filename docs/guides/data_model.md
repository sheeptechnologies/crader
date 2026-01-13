# Data model

Crader stores a code property graph in PostgreSQL. The schema is designed around immutable snapshots and content-addressable storage for chunk text.

## Core tables

- **repositories**: Unique by `(url, branch)`. `current_snapshot_id` points to the active snapshot.
- **snapshots**: One row per indexing run and commit hash. `status` is `pending`, `indexing`, `completed`, or `failed`. `file_manifest` stores the directory tree used by `CodeReader`.
- **files**: Files within a snapshot, including `language`, `category`, and parsing status.
- **contents**: Content-addressable storage for chunk text (`chunk_hash`), deduplicated across snapshots.
- **nodes**: Chunk metadata and byte ranges, referencing `files` and `contents`.
- **edges**: Directed relationships between nodes (`child_of`, `calls`, `defines`, `reads_from`, etc.).
- **nodes_fts**: Full-text search index built from chunk content and semantic tags.
- **node_embeddings**: Vector embeddings for chunks with denormalized fields for fast filtering.
- **staging_embeddings**: Unlogged table created during embedding runs for batching and deduplication.

## Core entities (Python)

The `crader.models` module mirrors the schema for most common objects:

- `Repository`, `Snapshot`, `FileRecord`
- `ChunkNode`, `ChunkContent`
- `CodeRelation`
- `ParsingResult`, `RetrievedContext`

`RetrievedContext` is the response type returned by the retriever and includes navigation hints and context expansion.
