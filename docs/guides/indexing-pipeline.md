# Indexing pipeline

This guide describes what happens when you call `CodebaseIndexer.index()`.

## Overview

The indexer performs a full scan of a repository commit and writes files, chunks, and relations into PostgreSQL. It does not generate embeddings; embeddings are handled by the separate `embed()` step.

## Steps

1. **Repository registration**
   - `PostgresGraphStorage.ensure_repository()` creates or updates the repository row.

2. **Git sync and snapshot creation**
   - `GitVolumeManager` updates a bare mirror of the repository.
   - `create_snapshot()` inserts a snapshot with status `indexing`.
   - If the same commit is already indexed, the existing snapshot is reused.

3. **File discovery via SourceCollector**
   - The [`SourceCollector`](source-collector.md) module handles all file enumeration.
   - Uses `git ls-files` for Git-native file discovery with zero-cost hashing.
   - Applies a four-stage filtering funnel:
     1. Git native discovery (tracked + untracked files)
     2. Metadata filtering (extensions, blocklist)
     3. Filesystem safety (symlinks, size limits)
     4. Semantic enrichment (category classification)
   - Files are yielded in batches as `CollectedFile` objects with:
     - `rel_path`, `full_path`, `extension`, `size_bytes`
     - `git_hash` (SHA-1 blob ID, enables cache-first workflows)
     - `category` (`source`, `test`, `config`, `docs`)
   - See [SourceCollector Guide](source-collector.md) for detailed configuration and API reference.

4. **Parallel parsing**
   - A `ProcessPoolExecutor` processes file chunks (50 files per task, 5 workers).
   - Each worker uses `TreeSitterRepoParser` to:
     - Skip large files (`>1 MB`), binaries, and minified/generated content.
     - Emit `FileRecord`, `ChunkNode`, `ChunkContent`, and `child_of` relations.
     - Build FTS documents from chunk metadata and content.

5. **Snapshot activation**
   - Indexing stats and a file manifest are generated.
   - The snapshot is marked `completed` and becomes the active snapshot.

## Notes

- If a repository is already being indexed, `index()` returns `"queued"`.
- Semantic tags from Tree-sitter queries are currently available for Python, JavaScript, and TypeScript.
- The SourceCollector's `git_hash` enables cache-first workflows: unchanged files (same hash in database) skip parsing entirely.
- Incremental indexing of stable codebases typically achieves 90%+ cache hit rates.
