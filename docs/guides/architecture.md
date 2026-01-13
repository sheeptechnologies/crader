# Architecture

Crader is split into a write path (indexing and embedding) and a read path (search and navigation). The system stores all data in PostgreSQL with snapshot isolation, so readers always query a stable version of the codebase.

## Write path

- **Repository sync**: `GitVolumeManager` maintains a bare mirror cache and creates ephemeral worktrees for specific commits. Repositories are stored under `CRADER_REPO_VOLUME` (default: `./sheep_data/repositories`).
- **Parsing and chunking**: `TreeSitterRepoParser` loads Tree-sitter grammars by extension and splits code into chunks. Chunks include byte ranges, line ranges, and metadata tags. Parent-child edges (`child_of`) are created during parsing.
- **SCIP relations**: `SCIPIndexer` runs language-specific SCIP tools to extract cross-file relations. Relations are resolved to node IDs and stored as edges in the graph. This is currently the bottleneck for file-incremental indexing; the roadmap includes replacing SCIP with Mycelium (stack graphs in Python): https://github.com/sheeptechnologies/mycelium.git.
- **Storage**: `PostgresGraphStorage` persists repositories, snapshots, files, nodes, contents, edges, and the FTS index.
- **Embeddings (separate step)**: `CodeEmbedder` stages node content, deduplicates by hash, then calls an embedding provider. Vectors are stored in `node_embeddings` for vector search.

## Read path

- **Search**: `CodeRetriever` runs vector search and keyword search through `SearchExecutor`. Results are merged using Reciprocal Rank Fusion.
- **Context expansion**: `GraphWalker` adds parent context and outgoing definitions based on graph edges.
- **Navigation and reading**: `CodeReader` reconstructs files from chunk content and uses the snapshot manifest for fast directory listing. `CodeNavigator` exposes helpers for neighbors, parent blocks, callers, and callees.

## Snapshot model

Each indexing run creates a snapshot tied to a commit hash. A repository points to a single active snapshot. Queries always target a specific snapshot, either explicitly or by resolving the active one.
