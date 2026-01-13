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

3. **File scan**
   - The worktree is scanned with `os.walk`.
   - Ignored directories: `.git`, `node_modules`, `__pycache__`, `.venv`, `dist`, `build`, `target`, `vendor`.
   - Extensions indexed:
     - `.py`, `.js`, `.jsx`, `.ts`, `.tsx`, `.java`, `.go`, `.rs`, `.c`, `.cpp`, `.php`, `.html`, `.css`

4. **Parallel parsing**
   - A `ProcessPoolExecutor` processes file chunks (50 files per task, 5 workers).
   - Each worker uses `TreeSitterRepoParser` to:
     - Skip large files (`>1 MB`), binaries, and minified/generated content.
     - Emit `FileRecord`, `ChunkNode`, `ChunkContent`, and `child_of` relations.
     - Build FTS documents from chunk metadata and content.

5. **SCIP relations**
   - `SCIPIndexer` runs in a parallel thread and extracts cross-file relations.
   - Relations are resolved to node IDs in the database by byte range.
   - This step is currently the bottleneck for file-incremental indexing; the roadmap includes replacing SCIP with Mycelium (stack graphs in Python): https://github.com/sheeptechnologies/mycelium.git.

6. **Snapshot activation**
   - Indexing stats and a file manifest are generated.
   - The snapshot is marked `completed` and becomes the active snapshot.

## Notes

- If a repository is already being indexed, `index()` returns `"queued"`.
- Semantic tags from Tree-sitter queries are currently available for Python, JavaScript, and TypeScript.
- Parsing and indexing always run on the full file set for the target commit.
