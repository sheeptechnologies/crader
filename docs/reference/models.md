# Data models

The `crader.models` module defines dataclasses used across the pipeline.

## Core entities

- `Repository`: repository identity and snapshot pointer.
- `Snapshot`: immutable version tied to a commit hash.
- `FileRecord`: metadata for a file in a snapshot, including parse status.
- `ChunkNode`: code chunk with byte range, line range, and metadata.
- `ChunkContent`: content-addressable storage for chunk text.
- `CodeRelation`: directed edge between two chunks.
- `ParsingResult`: aggregate container for parser output.

## Retrieval

- `RetrievedContext`: enriched search result returned by `CodeRetriever`.
  - Includes `content`, scores, semantic labels, and navigation hints.
  - `render()` returns a Markdown-formatted string.
