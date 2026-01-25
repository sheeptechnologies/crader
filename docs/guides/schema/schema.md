# Crader Database Schema Documentation

**Last Updated**: January 25, 2026  

---

## Table of Contents

1. [Entity-Relationship Diagram](#entity-relationship-diagram)
2. [Schema Overview](#schema-overview)
3. [Core Tables](#core-tables)
   - [repositories](#repositories)
   - [snapshots](#snapshots)
   - [files](#files)
   - [contents](#contents)
   - [code_chunks](#code_chunks)
   - [symbols](#symbols)
   - [code_chunk_embeddings](#code_chunk_embeddings)
   - [code_chunk_fts](#code_chunk_fts)
4. [Indexes](#indexes)
5. [Constraints](#constraints)
6. [Design Patterns](#design-patterns)
7. [Design Decisions](#design-decisions)
   - [PostgreSQL Extensions](#postgresql-extensions)
   - [Default Values for JSONB and ARRAY](#default-values-for-jsonb-and-array)
   - [Denormalization Consistency Enforcement](#denormalization-consistency-enforcement)
   - [Vector Hash: No Unique Constraint](#vector-hash-no-unique-constraint)
   - [Function Naming: Scoped Prefixes](#function-naming-scoped-prefixes)
   - [FTS Configuration](#fts-configuration-simple-vs-english)
   - [FTS Trigger Performance](#fts-trigger-performance)
   - [Vector Search Query Patterns](#vector-search-query-patterns)
8. [Migration Checklist](#migration-checklist)

---

## Entity-Relationship Diagram

```mermaid
erDiagram
    REPOSITORIES ||--o{ SNAPSHOTS : "has many"
    SNAPSHOTS ||--o{ FILES : "contains"
    FILES ||--o{ CODE_CHUNKS : "split into"
    CONTENTS ||--o{ CODE_CHUNKS : "referenced by"
    CODE_CHUNKS ||--o{ CODE_CHUNK_EMBEDDINGS : "embedded as"
    CODE_CHUNKS ||--o{ CODE_CHUNK_FTS : "indexed in"
    FILES ||--o{ SYMBOLS : "contains"
    SYMBOLS }o..o{ SYMBOLS : "hierarchy"
    CODE_CHUNKS }|..|{ SYMBOLS : "spatial overlap"
    SNAPSHOTS ||--o{ SYMBOLS : "denormalized"
    SNAPSHOTS ||--o{ CODE_CHUNK_FTS : "denormalized"
    FILES ||--o{ CODE_CHUNK_FTS : "file reference"
    SNAPSHOTS ||--o{ CODE_CHUNK_EMBEDDINGS : "denormalized"
    FILES ||--o{ CODE_CHUNK_EMBEDDINGS : "file reference"

    REPOSITORIES {
        uuid id PK
        text name
        text url
        jsonb metadata
        timestamptz created_at
        timestamptz updated_at
    }

    SNAPSHOTS {
        uuid id PK
        uuid repository_id FK
        text commit_hash
        jsonb metadata
        timestamptz created_at
    }

    FILES {
        uuid id PK
        uuid snapshot_id FK
        text path
        text language
        int size_bytes
        jsonb metadata
        timestamptz indexed_at
    }

    CONTENTS {
        text chunk_hash PK
        text content
        int size_bytes
    }

    CODE_CHUNKS {
        uuid id PK
        uuid file_id FK
        text chunk_hash FK
        int4range byte_range
        jsonb metadata
    }

    SYMBOLS {
        uuid id PK
        uuid file_id FK
        uuid snapshot_id FK
        text kind
        text name
        text[] scope_path
        text qualified_name
        int4range byte_range
        uuid parent_id FK
        jsonb metadata
    }

    CODE_CHUNK_EMBEDDINGS {
        uuid id PK
        uuid chunk_id FK
        uuid snapshot_id FK
        uuid file_id FK
        text vector_hash
        vector embedding
        text model
    }

    CODE_CHUNK_FTS {
        uuid chunk_id PK
        uuid snapshot_id FK
        uuid file_id FK
        tsvector search_vector
    }
```

---

## Schema Overview

The Crader v2 schema implements a **hybrid storage architecture** that separates:

- **Physical Layer**: How code is stored for RAG retrieval (`code_chunks`, `contents`)
- **Logical Layer**: How code is structured for navigation (`symbols`)
- **Search Indexes**: Optimized for different query patterns (`code_chunk_embeddings`, `code_chunk_fts`)

### Design Principles

1. **Content-Addressable Storage**: Deduplication via SHA256 hashing
2. **Snapshot Isolation**: All data scoped to Git commits
3. **File-Incremental**: Index changes at file granularity
4. **Denormalization**: Strategic duplication for query performance
5. **Spatial Indexing**: PostgreSQL range types for chunk↔symbol overlap

---

## Core Tables

### repositories

**Purpose**: Root entity representing a code repository being indexed.

**Columns**:

| Column | Type | Nullable | Description |
|--------|------|----------|-------------|
| `id` | `UUID` | NOT NULL | Primary key, unique repository identifier |
| `name` | `TEXT` | NOT NULL | Repository name (e.g., "crader") |
| `url` | `TEXT` | NULL | Git remote URL (e.g., "https://github.com/user/repo") |
| `metadata` | `JSONB` | NOT NULL | Flexible storage for repo-specific config |
| `created_at` | `TIMESTAMPTZ` | NOT NULL | When repository was first indexed |
| `updated_at` | `TIMESTAMPTZ` | NOT NULL | Last modification timestamp |

**Metadata Schema**:
```json
{
  "default_branch": "main",
  "languages": ["python", "typescript"],
  "indexing_config": {
    "ignore_patterns": ["*.min.js", "node_modules/**"],
    "chunk_size": 512
  }
}
```

**Constraints**:
- Primary Key: `id`
- Unique: `name` (repository names must be unique)

**Indexes**:
```sql
CREATE UNIQUE INDEX repositories_name_idx ON repositories(name);
```

**Usage Examples**:
```sql
-- Get repository by name
SELECT * FROM repositories WHERE name = 'crader';

-- List all repositories
SELECT id, name, url, created_at FROM repositories ORDER BY created_at DESC;
```

---

### snapshots

**Purpose**: Represents a Git commit snapshot of a repository at a specific point in time.

**Columns**:

| Column | Type | Nullable | Description |
|--------|------|----------|-------------|
| `id` | `UUID` | NOT NULL | Primary key, unique snapshot identifier |
| `repository_id` | `UUID` | NOT NULL | Foreign key to `repositories` |
| `commit_hash` | `TEXT` | NOT NULL | Full Git commit SHA (40 hex chars) |
| `metadata` | `JSONB` | NOT NULL | Snapshot-specific metadata (commit info, stats, etc.) |
| `created_at` | `TIMESTAMPTZ` | NOT NULL | When snapshot was indexed |

**Metadata Schema**:
```json
{
  "commit_timestamp": "2026-01-25T10:30:00Z",
  "commit_message": "Fix bug in tax calculator",
  "commit_author": "John Doe <john@example.com>",
  "tag": "v1.2.0",
  "branch": "main",
  "file_count": 342,
  "total_size_bytes": 1048576,
  "indexing_duration_ms": 5420
}
```

**Constraints**:
- Primary Key: `id`
- Foreign Key: `repository_id` → `repositories(id)` ON DELETE CASCADE
- Unique: `(repository_id, commit_hash)` (no duplicate snapshots for same commit)

**Indexes**:
```sql
CREATE INDEX snapshots_repo_idx ON snapshots(repository_id);
CREATE UNIQUE INDEX snapshots_unique_commit ON snapshots(repository_id, commit_hash);
```

**Usage Examples**:
```sql
-- Get latest snapshot for repository
SELECT * FROM snapshots 
WHERE repository_id = $repo_id 
ORDER BY created_at DESC 
LIMIT 1;

-- Find snapshot by commit hash
SELECT * FROM snapshots 
WHERE repository_id = $repo_id 
  AND commit_hash = $commit_hash;

-- List all tagged releases (from metadata)
SELECT 
    id,
    commit_hash,
    metadata->>'tag' as tag,
    metadata->>'commit_timestamp' as commit_time,
    metadata->>'commit_message' as message
FROM snapshots 
WHERE repository_id = $repo_id 
  AND metadata->>'tag' IS NOT NULL 
ORDER BY created_at DESC;
```

---

### files

**Purpose**: Represents individual source files within a snapshot.

**Columns**:

| Column | Type | Nullable | Description |
|--------|------|----------|-------------|
| `id` | `UUID` | NOT NULL | Primary key, unique file identifier |
| `snapshot_id` | `UUID` | NOT NULL | Foreign key to `snapshots` |
| `path` | `TEXT` | NOT NULL | Relative path from repository root (e.g., "src/index.py") |
| `language` | `TEXT` | NOT NULL | Programming language (e.g., "python", "typescript") |
| `size_bytes` | `INTEGER` | NOT NULL | File size in bytes |
| `metadata` | `JSONB` | NOT NULL | File-specific metadata |
| `indexed_at` | `TIMESTAMPTZ` | NOT NULL | When file was indexed |

**Metadata Schema**:
```json
{
  "encoding": "utf-8",
  "line_count": 245,
  "hash": "a3f5b8c...",
  "parser_version": "tree-sitter-python@0.20.0"
}
```

**Constraints**:
- Primary Key: `id`
- Foreign Key: `snapshot_id` → `snapshots(id)` ON DELETE CASCADE
- Unique: `(snapshot_id, path)` (no duplicate files in same snapshot)
- Check: `size_bytes >= 0`

**Indexes**:
```sql
CREATE INDEX files_snapshot_idx ON files(snapshot_id);
CREATE INDEX files_language_idx ON files(language);
CREATE INDEX files_path_idx ON files(snapshot_id, path);
CREATE INDEX files_path_pattern_idx ON files USING gin(path gin_trgm_ops);
```

**Usage Examples**:
```sql
-- Get all Python files in snapshot
SELECT * FROM files 
WHERE snapshot_id = $snapshot_id 
  AND language = 'python';

-- Find files by path pattern
SELECT * FROM files 
WHERE snapshot_id = $snapshot_id 
  AND path LIKE 'src/billing/%';

-- Get file by exact path
SELECT * FROM files 
WHERE snapshot_id = $snapshot_id 
  AND path = 'src/index.py';
```

---

### contents

**Purpose**: Content-addressable storage for deduplicated code text.

**Columns**:

| Column | Type | Nullable | Description |
|--------|------|----------|-------------|
| `chunk_hash` | `TEXT` | NOT NULL | SHA256 hash of content (primary key) |
| `content` | `TEXT` | NOT NULL | Actual code text |
| `size_bytes` | `INTEGER` | NOT NULL | Content size in bytes |

**Hash Computation**:
```python
import hashlib

def compute_chunk_hash(content: str) -> str:
    return hashlib.sha256(content.encode('utf-8')).hexdigest()
```

**Constraints**:
- Primary Key: `chunk_hash`
- Check: `size_bytes = length(content)` (enforced via trigger)
- Check: `size_bytes > 0`

**Indexes**:
```sql
-- Primary key index (automatic)
CREATE UNIQUE INDEX contents_pkey ON contents(chunk_hash);
```

**Design Rationale**:

**Deduplication**: Identical code blocks (vendored dependencies, generated files, copy-pasted utilities) share one content entry.

**Example**:
```
File A: "def helper(): pass"  → hash: abc123
File B: "def helper(): pass"  → hash: abc123 (same!)
File C: "def other(): pass"   → hash: def456

contents table:
  abc123 → "def helper(): pass"
  def456 → "def other(): pass"

Storage: 2 rows instead of 3 (33% reduction)
```

**Usage Examples**:
```sql
-- Insert content (idempotent via ON CONFLICT)
INSERT INTO contents (chunk_hash, content, size_bytes)
VALUES ($hash, $content, $size)
ON CONFLICT (chunk_hash) DO NOTHING;

-- Retrieve content by hash
SELECT content FROM contents WHERE chunk_hash = $hash;

-- Find duplicate content across files
SELECT chunk_hash, COUNT(*) as ref_count
FROM code_chunks
GROUP BY chunk_hash
HAVING COUNT(*) > 1
ORDER BY ref_count DESC;
```

---

### code_chunks

**Purpose**: Physical storage layer - text segments optimized for RAG retrieval.

**Columns**:

| Column | Type | Nullable | Description |
|--------|------|----------|-------------|
| `id` | `UUID` | NOT NULL | Primary key, unique chunk identifier |
| `file_id` | `UUID` | NOT NULL | Foreign key to `files` |
| `chunk_hash` | `TEXT` | NOT NULL | Foreign key to `contents` (actual text) |
| `byte_range` | `INT4RANGE` | NOT NULL | Byte range `[start, end)` within file |
| `metadata` | `JSONB` | NOT NULL | Chunk-specific metadata |

**Metadata Schema**:
```json
{
  "token_count": 384,
  "symbol_count": 3,
  "contains_defs": ["TaxCalculator.__init__", "TaxCalculator.calculate"],
  "contains_imports": ["logging", "decimal"],
  "enrichment_version": "v2"
}
```

**Constraints**:
- Primary Key: `id`
- Foreign Key: `file_id` → `files(id)` ON DELETE CASCADE
- Foreign Key: `chunk_hash` → `contents(chunk_hash)` ON DELETE RESTRICT
- Check: `NOT isempty(byte_range)`
- Check: `lower(byte_range) >= 0`

**Indexes**:
```sql
CREATE INDEX code_chunks_file_idx ON code_chunks(file_id);
CREATE INDEX code_chunks_hash_idx ON code_chunks(chunk_hash);
CREATE INDEX code_chunks_spatial_idx ON code_chunks USING gist(file_id, byte_range);
```

**Chunking Strategy**:

Chunks are created via **semantic block chunking** with token budget (typically 512-1024 tokens):

1. Parse file into top-level symbols (functions, classes)
2. Greedily pack symbols into chunks while under token limit
3. Respect symbol boundaries (never split mid-definition)
4. Create overflow chunks when symbol exceeds budget

**Example**:
```python
# File: billing.py (1500 tokens total)

# imports (10 tokens)
import logging
import decimal

# class TaxCalculator (400 tokens)
class TaxCalculator:
    def __init__(self): ...     # 50 tokens
    def calculate(self): ...    # 180 tokens
    def validate(self): ...     # 170 tokens

# function (600 tokens)
def process_batch(): ...

# function (300 tokens)
def helper_util(): ...

# Chunking result (budget: 512):
# Chunk 1 [0:450]:   imports + TaxCalculator.__init__ + calculate
# Chunk 2 [450:620]: TaxCalculator.validate
# Chunk 3 [620:1220]: process_batch (exceeds budget but atomic)
# Chunk 4 [1220:1500]: helper_util
```

**Usage Examples**:
```sql
-- Get all chunks for a file
SELECT c.*, co.content 
FROM code_chunks c
JOIN contents co ON co.chunk_hash = c.chunk_hash
WHERE c.file_id = $file_id
ORDER BY lower(c.byte_range);

-- Find chunks overlapping byte position
SELECT * FROM code_chunks
WHERE file_id = $file_id
  AND byte_range @> $position::int;

-- Get chunk with content
SELECT c.id, c.byte_range, c.metadata, co.content
FROM code_chunks c
JOIN contents co ON co.chunk_hash = c.chunk_hash
WHERE c.id = $chunk_id;
```

---

### symbols

**Purpose**: Logical structure layer - definitions, imports, exports for code navigation.

**Columns**:

| Column | Type | Nullable | Description |
|--------|------|----------|-------------|
| `id` | `UUID` | NOT NULL | Primary key, unique symbol identifier |
| `file_id` | `UUID` | NOT NULL | Foreign key to `files` |
| `snapshot_id` | `UUID` | NOT NULL | Denormalized from files for fast filtering |
| `kind` | `TEXT` | NOT NULL | Symbol type (see below) |
| `name` | `TEXT` | NOT NULL | Symbol name (e.g., "calculate_tax") |
| `scope_path` | `TEXT[]` | NOT NULL | Hierarchical scope (e.g., `['billing', 'TaxCalculator']`) |
| `qualified_name` | `TEXT` | GENERATED | Computed: `array_to_string(scope_path, '.') || '.' || name` |
| `byte_range` | `INT4RANGE` | NOT NULL | Position within file |
| `parent_id` | `UUID` | NULL | Self-referencing FK for hierarchy (e.g., method → class) |
| `metadata` | `JSONB` | NOT NULL | Kind-specific metadata (signatures, types, etc.) |

**Symbol Kinds**:

| Kind | Description | Examples |
|------|-------------|----------|
| `function` | Top-level function definition | `def calculate_tax()` |
| `class` | Class definition | `class TaxCalculator` |
| `method` | Class method | `def __init__(self)` inside a class |
| `variable` | Module-level variable/constant | `TAX_RATE = 0.2` |
| `import` | Import statement | `from decimal import Decimal` |
| `export` | Export declaration (TypeScript/ES6) | `export { TaxCalculator }` |

**Metadata Schemas by Kind**:

**Definitions** (function, class, method):
```json
{
  "signature": "(amount: float, tax_rate: float) -> Decimal",
  "return_type": "Decimal",
  "docstring": "Calculate tax on amount applying rate",
  "parameters": [
    {"name": "amount", "type": "float"},
    {"name": "tax_rate", "type": "float"}
  ],
  "is_async": false,
  "decorators": ["@staticmethod"]
}
```

**Imports**:
```json
{
  "import_path": "from decimal import Decimal",
  "resolved_files": ["<stdlib>"],
  "is_relative": false,
  "confidence": "high"
}
```

**Exports**:
```json
{
  "exported_as": "named",
  "export_name": "TaxCalculator"
}
```

**Constraints**:
- Primary Key: `id`
- Foreign Key: `file_id` → `files(id)` ON DELETE CASCADE
- Foreign Key: `snapshot_id` → `snapshots(id)` ON DELETE CASCADE
- Foreign Key: `parent_id` → `symbols(id)` ON DELETE CASCADE (nullable)
- Check: `parent_id != id` (no self-references)
- Check: `kind IN ('function', 'class', 'method', 'variable', 'import', 'export')`
- Check: `NOT isempty(byte_range)`

**Indexes**:
```sql
-- Partial index for definitions (hot path)
CREATE INDEX symbols_defs_idx 
ON symbols(snapshot_id, qualified_name) 
WHERE kind IN ('function', 'class', 'method');

-- Partial index for imports
CREATE INDEX symbols_imports_idx 
ON symbols(snapshot_id, name, file_id) 
WHERE kind = 'import';

-- Spatial index for chunk enrichment
CREATE INDEX symbols_spatial_idx 
ON symbols USING gist(file_id, byte_range);

-- Hierarchy traversal
CREATE INDEX symbols_parent_idx 
ON symbols(parent_id) 
WHERE parent_id IS NOT NULL;

-- Name search
CREATE INDEX symbols_name_idx 
ON symbols(snapshot_id, name);
```

**Denormalization Rationale**:

**Without `snapshot_id`** (normalized):
```sql
SELECT s.* FROM symbols s 
JOIN files f ON f.id = s.file_id 
WHERE f.snapshot_id = $snapshot_id AND s.name = 'calculateTax';
```
→ Requires JOIN, scans files table

**With `snapshot_id`** (denormalized):
```sql
SELECT * FROM symbols 
WHERE snapshot_id = $snapshot_id AND name = 'calculateTax';
```
→ Direct filter, uses index only

On repositories with 10k+ files, this reduces query latency from 50-100ms to 5-10ms.

**Usage Examples**:
```sql
-- Find all functions named "calculate_tax" in snapshot
SELECT * FROM symbols
WHERE snapshot_id = $snapshot_id
  AND kind = 'function'
  AND name = 'calculate_tax';

-- Get class hierarchy (class + all methods)
WITH RECURSIVE hierarchy AS (
  SELECT * FROM symbols WHERE id = $class_id
  UNION ALL
  SELECT s.* FROM symbols s
  JOIN hierarchy h ON s.parent_id = h.id
)
SELECT * FROM hierarchy;

-- Find symbols overlapping with chunk
SELECT s.* FROM symbols s
JOIN code_chunks c ON c.file_id = s.file_id
WHERE c.id = $chunk_id
  AND s.byte_range && c.byte_range;

-- Which files import "Decimal"?
SELECT DISTINCT f.path 
FROM symbols s
JOIN files f ON f.id = s.file_id
WHERE s.snapshot_id = $snapshot_id
  AND s.kind = 'import'
  AND s.name = 'Decimal';
```

---

### code_chunk_embeddings

**Purpose**: Maps code chunks to semantic vector representations for similarity search.

**Columns**:

| Column | Type | Nullable | Description |
|--------|------|----------|-------------|
| `id` | `UUID` | NOT NULL | Primary key, unique embedding identifier |
| `chunk_id` | `UUID` | NOT NULL | Foreign key to `code_chunks` |
| `snapshot_id` | `UUID` | NOT NULL | Denormalized for snapshot filtering |
| `file_id` | `UUID` | NOT NULL | Denormalized for file-scoped operations |
| `vector_hash` | `TEXT` | NOT NULL | SHA256 of enriched text (cache key) |
| `embedding` | `VECTOR` | NOT NULL | Actual vector (pgvector extension) |
| `model` | `TEXT` | NOT NULL | Model identifier (e.g., "text-embedding-3-small") |

**Vector Hash Computation**:
```python
def compute_vector_hash(enriched_text: str, enrichment_version: str) -> str:
    payload = f"{enrichment_version}|{enriched_text}"
    return hashlib.sha256(payload.encode('utf-8')).hexdigest()
```

**Enrichment Process**:
1. Start with raw chunk text
2. Query overlapping symbols (spatial query on `symbols` table)
3. Append function signatures, docstrings, import statements
4. Hash enriched text → `vector_hash`
5. Check cache: "Do we have an embedding for this hash?"
6. Cache miss → Call embedding API → Store with hash
7. Cache hit → Reuse existing vector

**Constraints**:
- Primary Key: `id`
- Foreign Key: `chunk_id` → `code_chunks(id)` ON DELETE CASCADE
- Foreign Key: `snapshot_id` → `snapshots(id)` ON DELETE CASCADE
- Foreign Key: `file_id` → `files(id)` ON DELETE CASCADE
- Unique: `(chunk_id, model)` (prevent duplicate embeddings on re-indexing/retries)

**Design Note**: No unique constraint on `(vector_hash, model)`. The same `vector_hash` can appear multiple times (e.g., vendored code across files, unchanged code across snapshots). This enables global embedding cache deduplication.

**Indexes**:
```sql
-- Vector similarity search (HNSW)
-- Preferred over IVFFlat for Crader's commit-driven mutable workload
-- HNSW handles frequent insert/delete better without requiring periodic rebuilds
CREATE INDEX embeddings_vector_idx 
ON code_chunk_embeddings 
USING hnsw (embedding vector_cosine_ops)
WITH (m = 16, ef_construction = 64);

-- Cache lookups: composite index for (vector_hash, model)
-- Query pattern: WHERE vector_hash = $hash AND model = $model
CREATE INDEX embeddings_cache_key_idx 
ON code_chunk_embeddings(vector_hash, model);

-- Idempotency: prevent duplicate embeddings for same chunk+model
CREATE UNIQUE INDEX uq_embeddings_chunk_model
ON code_chunk_embeddings(chunk_id, model);

-- Snapshot filtering
CREATE INDEX embeddings_snapshot_idx 
ON code_chunk_embeddings(snapshot_id);

-- File-scoped operations
CREATE INDEX embeddings_file_idx 
ON code_chunk_embeddings(file_id);
```

**Vector Index Strategy: Why HNSW?**

Crader uses **HNSW (Hierarchical Navigable Small World)** instead of IVFFlat for vector similarity search.

**Rationale**:
- **Commit-driven workload**: Each Git commit creates a new snapshot with new embeddings
- **Mutable at table level**: Frequent inserts (new snapshots) and deletes (GC old snapshots)
- **Bounded size**: Aggressive snapshot GC keeps table size limited (~5 snapshots per branch)
- **Query pattern**: All queries scoped by `snapshot_id`, so dataset is effectively partitioned

**HNSW advantages for this workload**:
- No periodic rebuilds required (IVFFlat degrades with insert/delete)
- Incremental updates maintain quality
- Better recall stability over time
- Simpler operations (no manual REINDEX needed)

**HNSW parameters**:
- `m = 16`: Bidirectional links per layer (default, good for 1536-dim vectors)
- `ef_construction = 64`: Build-time candidate list size (balanced speed/quality)

**Query-time tuning** (application-level):
```sql
SET hnsw.ef_search = 100;  -- Default: 40
-- Higher = better recall, slower queries
-- Tune per deployment based on recall requirements
```

**Alternative**: IVFFlat would be preferable for:
- Mostly-static datasets (rare inserts/deletes)
- Willingness to rebuild index periodically (e.g., `REINDEX CONCURRENTLY`)
- Very large datasets where HNSW memory overhead is prohibitive

For Crader's snapshot-based lifecycle, HNSW provides better operational simplicity and consistent recall without manual maintenance.

**Denormalization Rationale**:

Both `snapshot_id` and `file_id` are denormalized to enable efficient filtering:

```sql
-- Snapshot-scoped vector search (common query)
SELECT chunk_id, embedding <=> $query_vector AS distance
FROM code_chunk_embeddings
WHERE snapshot_id = $snapshot_id
ORDER BY embedding <=> $query_vector
LIMIT 10;
-- Without snapshot_id denormalization, requires JOIN with code_chunks → files

-- File-scoped operations (incremental indexing)
DELETE FROM code_chunk_embeddings 
WHERE file_id = $file_id;
-- "Re-embed only changed files"

SELECT file_id, COUNT(*) 
FROM code_chunk_embeddings 
WHERE snapshot_id = $snapshot_id
GROUP BY file_id;
-- "Show embedding coverage by file"
```

**Vector Caching**:

Cache hit rate: 60-80% during re-indexing of stable codebases.

**Example**:
```
# Snapshot 1 (commit abc)
File A: "def helper(): return 42"
  → enriched: "function helper() -> int\ndef helper(): return 42"
  → vector_hash: xyz789
  → Embedding API called → store embedding

# Snapshot 2 (commit def) - File A unchanged
File A: "def helper(): return 42"
  → enriched: (same as before)
  → vector_hash: xyz789 (cache hit!)
  → Reuse embedding from snapshot 1 (no API call)
```

**Usage Examples**:
```sql
-- Vector similarity search
SELECT 
  c.id,
  f.path,
  co.content,
  e.embedding <=> $query_vector AS distance
FROM code_chunk_embeddings e
JOIN code_chunks c ON c.id = e.chunk_id
JOIN files f ON f.id = c.file_id
JOIN contents co ON co.chunk_hash = c.chunk_hash
WHERE e.snapshot_id = $snapshot_id
ORDER BY e.embedding <=> $query_vector
LIMIT 10;

-- Cache lookup before embedding
SELECT embedding 
FROM code_chunk_embeddings
WHERE vector_hash = $hash
  AND model = $model
LIMIT 1;

-- Re-embed changed files only
DELETE FROM code_chunk_embeddings
WHERE file_id IN (
  SELECT id FROM files 
  WHERE snapshot_id = $new_snapshot_id
    AND path IN ($changed_paths)
);
```

---

### code_chunk_fts

**Purpose**: Full-text search index for lexical (keyword) search.

**Columns**:

| Column | Type | Nullable | Description |
|--------|------|----------|-------------|
| `chunk_id` | `UUID` | NOT NULL | Primary key and FK to `code_chunks` |
| `snapshot_id` | `UUID` | NOT NULL | Denormalized for snapshot filtering |
| `file_id` | `UUID` | NOT NULL | FK to files for display/JOIN optimization |
| `search_vector` | `TSVECTOR` | NOT NULL | PostgreSQL full-text search index |

**Search Vector Generation**:

Automatically maintained via trigger on `code_chunks`:

```sql
CREATE OR REPLACE FUNCTION update_fts_vector()
RETURNS TRIGGER AS $$
BEGIN
  INSERT INTO code_chunk_fts (chunk_id, snapshot_id, file_id, search_vector)
  SELECT 
    NEW.id,
    f.snapshot_id,
    NEW.file_id,
    to_tsvector('english', co.content)
  FROM contents co
  JOIN files f ON f.id = NEW.file_id
  WHERE co.chunk_hash = NEW.chunk_hash
  ON CONFLICT (chunk_id) DO UPDATE
  SET search_vector = EXCLUDED.search_vector;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER code_chunks_fts_trigger
AFTER INSERT OR UPDATE ON code_chunks
FOR EACH ROW EXECUTE FUNCTION update_fts_vector();
```

**Constraints**:
- Primary Key: `chunk_id`
- Foreign Key: `chunk_id` → `code_chunks(id)` ON DELETE CASCADE
- Foreign Key: `snapshot_id` → `snapshots(id)` ON DELETE CASCADE
- Foreign Key: `file_id` → `files(id)` ON DELETE CASCADE

**Indexes**:
```sql
-- Full-text search
CREATE INDEX fts_search_idx 
ON code_chunk_fts 
USING gin(search_vector);

-- Snapshot filtering
CREATE INDEX fts_snapshot_idx 
ON code_chunk_fts(snapshot_id);

-- File reference (for JOINs)
CREATE INDEX fts_file_idx 
ON code_chunk_fts(file_id);
```

**Denormalization Rationale**:

- `snapshot_id`: Denormalized for filtering (eliminates JOIN during search)
- `file_id`: FK for display (JOIN with files needed anyway for complete results)

**Query Pattern**:
```sql
-- Lexical search with snapshot filtering
SELECT 
  c.id,
  f.path,
  co.content,
  ts_rank(fts.search_vector, query) AS rank
FROM code_chunk_fts fts
JOIN code_chunks c ON c.id = fts.chunk_id
JOIN files f ON f.id = fts.file_id
JOIN contents co ON co.chunk_hash = c.chunk_hash,
     to_tsquery('english', $query_string) query
WHERE fts.snapshot_id = $snapshot_id
  AND fts.search_vector @@ query
ORDER BY rank DESC
LIMIT 20;
```

**Usage Examples**:
```sql
-- Search for "calculate tax"
SELECT chunk_id, ts_rank(search_vector, query) AS rank
FROM code_chunk_fts,
     to_tsquery('english', 'calculate & tax') AS query
WHERE snapshot_id = $snapshot_id
  AND search_vector @@ query
ORDER BY rank DESC;

-- Search with phrase matching
SELECT chunk_id
FROM code_chunk_fts
WHERE snapshot_id = $snapshot_id
  AND search_vector @@ phraseto_tsquery('english', 'OAuth2 authentication');

-- File-scoped search
SELECT c.id, co.content
FROM code_chunk_fts fts
JOIN code_chunks c ON c.id = fts.chunk_id
JOIN contents co ON co.chunk_hash = c.chunk_hash
WHERE fts.snapshot_id = $snapshot_id
  AND fts.file_id = $file_id
  AND fts.search_vector @@ to_tsquery('calculateTax');
```

---

## Indexes

### Performance-Critical Indexes

**Snapshot Isolation** (most queries filter by snapshot):
```sql
CREATE INDEX snapshots_repo_commit_idx ON snapshots(repository_id, commit_hash);
CREATE INDEX files_snapshot_path_idx ON files(snapshot_id, path);
CREATE INDEX symbols_snapshot_name_idx ON symbols(snapshot_id, name);
CREATE INDEX embeddings_snapshot_idx ON code_chunk_embeddings(snapshot_id);
CREATE INDEX fts_snapshot_idx ON code_chunk_fts(snapshot_id);
```

**Spatial Queries** (chunk↔symbol overlap):
```sql
CREATE INDEX code_chunks_spatial_idx ON code_chunks USING gist(file_id, byte_range);
CREATE INDEX symbols_spatial_idx ON symbols USING gist(file_id, byte_range);
```

**Vector Search** (HNSW):
```sql
CREATE INDEX embeddings_vector_idx
ON code_chunk_embeddings
USING hnsw (embedding vector_cosine_ops)
WITH (m = 16, ef_construction = 64);
```

**Full-Text Search**:
```sql
CREATE INDEX fts_search_idx ON code_chunk_fts USING gin(search_vector);
```

### Partial Indexes

**Symbol Definitions** (5% of table, 80% of queries):
```sql
CREATE INDEX symbols_defs_idx 
ON symbols(snapshot_id, qualified_name) 
WHERE kind IN ('function', 'class', 'method');
```

**Symbol Imports** (cross-file navigation):
```sql
CREATE INDEX symbols_imports_idx 
ON symbols(snapshot_id, name, file_id) 
WHERE kind = 'import';
```

**Symbol Hierarchy** (class → method relationships):
```sql
CREATE INDEX symbols_parent_idx 
ON symbols(parent_id) 
WHERE parent_id IS NOT NULL;
```

---

## Constraints

### Referential Integrity

**CASCADE on snapshot deletion**:
```sql
-- Deleting a snapshot removes all associated data
snapshots.id ← files.snapshot_id (CASCADE)
files.id ← code_chunks.file_id (CASCADE)
files.id ← symbols.file_id (CASCADE)
code_chunks.id ← code_chunk_embeddings.chunk_id (CASCADE)
code_chunks.id ← code_chunk_fts.chunk_id (CASCADE)
```

**RESTRICT on content deletion**:
```sql
-- Cannot delete content still referenced by chunks
contents.chunk_hash ← code_chunks.chunk_hash (RESTRICT)
```

### Business Logic Constraints

**No self-referencing symbols**:
```sql
ALTER TABLE symbols ADD CONSTRAINT no_self_parent 
CHECK (parent_id IS NULL OR parent_id != id);
```

**Valid symbol kinds**:
```sql
ALTER TABLE symbols ADD CONSTRAINT valid_kind 
CHECK (kind IN ('function', 'class', 'method', 'variable', 'import', 'export'));
```

**Non-empty ranges**:
```sql
ALTER TABLE code_chunks ADD CONSTRAINT valid_range 
CHECK (NOT isempty(byte_range) AND lower(byte_range) >= 0);

ALTER TABLE symbols ADD CONSTRAINT valid_range 
CHECK (NOT isempty(byte_range) AND lower(byte_range) >= 0);
```

**Content size consistency**:
```sql
CREATE TRIGGER enforce_content_size
BEFORE INSERT OR UPDATE ON contents
FOR EACH ROW
EXECUTE FUNCTION check_content_size();

-- Function verifies: size_bytes = length(content)
```

### Uniqueness Constraints

**No duplicate snapshots**:
```sql
CREATE UNIQUE INDEX snapshots_unique_commit 
ON snapshots(repository_id, commit_hash);
```

**No duplicate files per snapshot**:
```sql
CREATE UNIQUE INDEX files_unique_path 
ON files(snapshot_id, path);
```

**No duplicate embeddings per chunk and model** (idempotency):
```sql
CREATE UNIQUE INDEX uq_embeddings_chunk_model
ON code_chunk_embeddings(chunk_id, model);
```

Note: There is intentionally **no** unique constraint on `(vector_hash, model)` to enable global embedding cache across files and snapshots. See [Design Decisions](#vector-hash-no-unique-constraint).

---

## Design Patterns

### 1. Content-Addressable Storage

**Pattern**: Store content once, reference by hash.

**Implementation**:
```sql
-- Step 1: Compute hash
chunk_hash = SHA256(content)

-- Step 2: Insert (idempotent)
INSERT INTO contents (chunk_hash, content, size_bytes)
VALUES ($hash, $content, $size)
ON CONFLICT (chunk_hash) DO NOTHING;

-- Step 3: Reference
INSERT INTO code_chunks (id, file_id, chunk_hash, byte_range, metadata)
VALUES (gen_random_uuid(), $file_id, $hash, $range, $metadata);
```

**Deduplication Example**:
```
3 files with identical utility function:
  File A: chunk_hash → abc123
  File B: chunk_hash → abc123
  File C: chunk_hash → abc123

contents table: 1 row (abc123)
code_chunks table: 3 rows (all referencing abc123)

Storage: 1 content copy instead of 3
```

### 2. Snapshot Isolation

**Pattern**: All queries scoped to a specific Git commit.

**Implementation**:
```sql
-- Every query includes snapshot filter
SELECT * FROM symbols 
WHERE snapshot_id = $snapshot_id 
  AND name = 'calculate_tax';

-- Denormalization enables direct filtering
-- (no JOIN with files → snapshots)
```

**Benefit**: Time-travel queries, A/B comparison, historical analysis.

### 3. Spatial Indexing

**Pattern**: Use PostgreSQL range types for efficient overlap queries.

**Implementation**:
```sql
-- Chunk: [100, 500)
-- Symbol: [200, 300)

-- Overlap query using GiST index
SELECT s.* FROM symbols s
WHERE s.file_id = $file_id
  AND s.byte_range && int4range(100, 500);

-- Returns symbols [200, 300) because it overlaps [100, 500)
```

**Use Cases**:
- Find symbols within chunk (for enrichment)
- Find chunks containing symbol (for context)
- Verify no overlapping chunks (data validation)

### 4. Denormalization for Query Performance

**Pattern**: Duplicate foreign keys in derived tables to avoid JOINs.

**Where Applied**:
- `symbols.snapshot_id` (from files → snapshots)
- `code_chunk_embeddings.snapshot_id` (from chunks → files → snapshots)
- `code_chunk_embeddings.file_id` (from chunks → files)
- `code_chunk_fts.snapshot_id` (from chunks → files → snapshots)

**Maintenance**: Database triggers keep denormalized fields synchronized.

**Trade-off**:
- ✅ 10x faster queries (eliminate JOINs in hot path)
- ❌ Slightly larger storage (~5-10%)
- ❌ Trigger overhead on writes (negligible for batch indexing)

### 5. Partial Indexes

**Pattern**: Index only subset of rows for specific query patterns.

**Implementation**:
```sql
-- Only index definitions (5% of symbols)
CREATE INDEX symbols_defs_idx 
ON symbols(snapshot_id, qualified_name) 
WHERE kind IN ('function', 'class', 'method');

-- 20x smaller index, same query performance
```

**Where Applied**:
- Symbol definitions vs imports
- Parent-child relationships vs top-level symbols
- Tagged snapshots vs regular commits

### 6. Vector Hash Caching

**Pattern**: Avoid redundant embedding API calls via content-based deduplication.

**Implementation**:
```python
# Before calling embedding API:
vector_hash = SHA256(enrichment_version + "|" + enriched_text)

existing = db.query(
    "SELECT embedding FROM code_chunk_embeddings "
    "WHERE vector_hash = ? AND model = ?",
    vector_hash, model
)

if existing:
    return existing.embedding  # Cache hit
else:
    embedding = call_embedding_api(enriched_text)
    db.insert(vector_hash, embedding, model)
    return embedding
```

**Cache Hit Rate**: 60-80% during re-indexing (empirical).

---

## Design Decisions

This section explains key design decisions made during the v2 schema migration (`c7afc7db3cb4_initial_schema.py`).

### PostgreSQL Extensions

#### pgcrypto for gen_random_uuid()

**Decision**: Use `gen_random_uuid()` for UUID generation.

**Rationale**:
- PostgreSQL 13+ has `gen_random_uuid()` built-in
- For PostgreSQL 12 and below, it requires `pgcrypto` extension
- Adding `CREATE EXTENSION IF NOT EXISTS pgcrypto` ensures compatibility

**Alternative considered**: `uuid-ossp` extension with `uuid_generate_v4()`
- Rejected: `pgcrypto` is more modern and will be native in all future PG versions

#### pg_trgm for Fuzzy Path Search

**Use case**: Enable queries like `WHERE path % 'billing'` for fuzzy file path matching.

**Index**: `CREATE INDEX ... USING gin(path gin_trgm_ops)`

---

### Default Values for JSONB and ARRAY

#### Problem: Implicit String Casting

Using `server_default='{}'` for JSONB or ARRAY columns is unreliable:
- PostgreSQL sometimes interprets `'{}'` as string, not JSONB/array
- Behavior depends on context and PG version
- Can cause subtle bugs in production

#### Solution: Explicit Type Casting

```python
# JSONB
sa.Column('metadata', postgresql.JSONB(...),
          server_default=sa.text("'{}'::jsonb"), nullable=False)

# ARRAY(text)
sa.Column('scope_path', postgresql.ARRAY(sa.String()),
          server_default=sa.text("'{}'::text[]"), nullable=False)
```

**Rationale**:
- `sa.text("'{}'::jsonb")` ensures PostgreSQL interprets it as JSONB
- `sa.text("'{}'::text[]")` ensures array type
- Explicit > implicit

---

### Denormalization Consistency Enforcement

#### The Problem

Tables have denormalized foreign keys for query performance:

**symbols**:
- `file_id` → `files.id`
- `snapshot_id` → denormalized from `files.snapshot_id`

**code_chunk_embeddings**:
- `chunk_id` → `code_chunks.id`
- `file_id` → denormalized from `code_chunks.file_id`
- `snapshot_id` → denormalized from `files.snapshot_id` (via `file_id`)

**Risk**: Application bug could insert inconsistent data:
```python
# BUG: snapshot_id doesn't match file's snapshot_id
insert_symbol(
    file_id='file-in-snapshot-A',
    snapshot_id='snapshot-B',  # WRONG!
    ...
)
```

This would be **silently accepted** without enforcement, leading to:
- Queries missing data (wrong snapshot filter)
- Cross-snapshot pollution
- Data integrity violations

#### Solution: Database Triggers

**symbols consistency check**:
```sql
CREATE FUNCTION crader_symbols_check_snapshot()
RETURNS TRIGGER AS $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM files f
        WHERE f.id = NEW.file_id
          AND f.snapshot_id = NEW.snapshot_id
    ) THEN
        RAISE EXCEPTION 'symbols.snapshot_id must match files.snapshot_id';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
```

**code_chunk_embeddings consistency check**:
```sql
CREATE FUNCTION crader_embeddings_check_refs()
RETURNS TRIGGER AS $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM code_chunks c
        JOIN files f ON f.id = c.file_id
        WHERE c.id = NEW.chunk_id
          AND c.file_id = NEW.file_id
          AND f.snapshot_id = NEW.snapshot_id
    ) THEN
        RAISE EXCEPTION 'embeddings snapshot_id/file_id must match chunk';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
```

#### Performance Impact

| Metric | Cost |
|--------|------|
| Per INSERT | ~1ms (index lookups) |
| Batch operations (10k rows) | ~0.5-1% overhead |
| UPDATE | Only fires if `file_id`/`snapshot_id` changes |

**Trade-off**: Tiny performance cost for **guaranteed data integrity**.

#### Alternative Considered: Remove Denormalization

**Rejected because** query performance is critical:

Without denormalization:
```sql
-- Requires JOIN on every search query
SELECT * FROM symbols s
JOIN files f ON f.id = s.file_id
WHERE f.snapshot_id = $snapshot_id AND s.name = 'foo';
```

With denormalization:
```sql
-- Direct filter, uses index
SELECT * FROM symbols
WHERE snapshot_id = $snapshot_id AND name = 'foo';
```

**Performance difference**: 5-10ms vs 50-100ms on large repos (10k+ files).

---

### Vector Hash: No Unique Constraint

#### Original Plan

```python
# Unique constraint to prevent duplicate embeddings
op.create_index('ix_embeddings_unique_hash', 'code_chunk_embeddings',
               ['vector_hash', 'model'], unique=True)
```

#### Problem

`vector_hash` is a **global cache key** for reusing embeddings:

```python
vector_hash = SHA256(enrichment_version + "|" + enriched_text)
```

**Use case**: Same code in different files/snapshots should reuse embedding:

```python
# File A, snapshot 1
def helper(): return 42
# → vector_hash: abc123
# → Call embedding API → store

# File B, snapshot 1 (vendored copy)
def helper(): return 42
# → vector_hash: abc123 (CACHE HIT!)
# → Reuse embedding from File A (no API call)

# File A, snapshot 2 (unchanged)
def helper(): return 42
# → vector_hash: abc123 (CACHE HIT!)
# → Reuse embedding from snapshot 1
```

**With unique constraint**: Second insert would fail → cache broken.

#### Decision

**Remove unique constraint on (vector_hash, model)**.

**Rationale**:
- Multiple rows can have same `vector_hash` (intentional deduplication)
- Cache lookup: `SELECT embedding WHERE vector_hash = $hash LIMIT 1`
- Savings: 60-80% fewer API calls (empirical measurement)

---

### Function Naming: Scoped Prefixes

#### Problem: Namespace Pollution

Generic function names can conflict:
```sql
CREATE FUNCTION update_updated_at() ...  -- Too generic
```

#### Solution: Prefix with `crader_`

```sql
CREATE FUNCTION crader_update_updated_at() ...
CREATE FUNCTION crader_symbols_check_snapshot() ...
CREATE FUNCTION crader_embeddings_check_refs() ...
CREATE FUNCTION crader_update_fts_vector() ...
```

**Benefits**:
- Clear ownership
- Avoids conflicts with other applications
- Easy to find (`\df crader_*` in psql)

---

### FTS Configuration: 'simple' vs 'english'

#### Problem with 'english'

PostgreSQL's `to_tsvector('english', text)` applies stemming:
- "running" → "run"
- "classes" → "class"
- "authenticated" → "authent"

**Why this is bad for code**:
- Variable names: `runningTotal` vs `run` (different semantics)
- Function names: `authenticate()` vs `authent` (lose precision)

#### Solution: Use 'simple'

```sql
to_tsvector('simple', co.content)
```

**What 'simple' does**:
- Basic tokenization (split on whitespace/punctuation)
- Case folding (uppercase → lowercase)
- **No stemming**
- **No stop words removal**

**Trade-off**:
- ✅ Precise matching for code identifiers
- ✅ No false positives from stemming
- ❌ No fuzzy matching on natural language comments
- ❌ Slightly larger tsvector

---

### FTS Trigger Performance

#### Trigger Logic

```sql
CREATE FUNCTION crader_update_fts_vector()
RETURNS TRIGGER AS $$
BEGIN
    INSERT INTO code_chunk_fts (chunk_id, snapshot_id, file_id, search_vector)
    SELECT
        NEW.id,
        f.snapshot_id,
        NEW.file_id,
        to_tsvector('simple', co.content)
    FROM contents co
    JOIN files f ON f.id = NEW.file_id
    WHERE co.chunk_hash = NEW.chunk_hash
    ...
END;
```

#### Performance Cost

| Operation | Time |
|-----------|------|
| Lookups (contents + files) | ~0.5ms |
| `to_tsvector` | ~1-2ms |
| FTS GIN index update | ~0.5ms |
| **Total per chunk** | ~2-3ms |

**For bulk indexing (10k chunks)**:
- Without trigger: ~5 seconds
- With trigger: ~25-30 seconds
- **Overhead**: ~5x

#### Why This Is Acceptable

- Indexing happens asynchronously (background job)
- Not blocking user requests
- Throughput: ~300-400 chunks/sec on modest hardware

**Alternative considered**: Separate FTS population step
- Rejected: More complexity, risk of FTS getting out of sync

---

### Vector Search Query Patterns

#### Cache Lookup: Composite Index

**Query pattern**:
```sql
SELECT embedding
FROM code_chunk_embeddings
WHERE vector_hash = $hash AND model = $model
LIMIT 1;
```

**Index**:
```sql
CREATE INDEX ix_embeddings_cache_key
ON code_chunk_embeddings (vector_hash, model);
```

**Why composite**: Query always filters by BOTH columns.

#### Idempotency: Unique Constraint on (chunk_id, model)

**Problem**: Re-indexing or job retries could create duplicates.

**Solution**:
```sql
CREATE UNIQUE INDEX uq_embeddings_chunk_model
ON code_chunk_embeddings (chunk_id, model);
```

**Behavior**:
- First insert: succeeds
- Retry insert: fails with unique violation
- Application catches error → skips (already embedded)

#### Vector Hash Composition

`vector_hash` does NOT include model identifier (by design):

```python
# Actual implementation
vector_hash = SHA256(f"{enrichment_version}|{enriched_text}")

# NOT: SHA256(f"{enrichment_version}|{model}|{enriched_text}")
```

**Rationale**:
- Same enriched text → same vector_hash
- Model differentiation handled by composite index `(vector_hash, model)`
- Enables cross-model cache hits if embeddings are compatible

#### Snapshot-Filtered Vector Search

**Query pattern**:
```sql
SELECT chunk_id, embedding <=> $query_vector AS distance
FROM code_chunk_embeddings
WHERE snapshot_id = $snapshot_id
ORDER BY embedding <=> $query_vector
LIMIT 10;
```

**Performance tuning**: `hnsw.ef_search`

```sql
SET hnsw.ef_search = 40;   -- Default (balanced)
SET hnsw.ef_search = 100;  -- Better recall under filtering
SET hnsw.ef_search = 20;   -- Faster search (lower recall)
```

**Target**: 90-95% recall for typical workloads.

---

### Design Decisions Summary

| Issue | Decision | Rationale |
|-------|----------|-----------|
| UUID generation | `pgcrypto` extension | PG <13 compatibility |
| JSONB/ARRAY defaults | Explicit `::jsonb`, `::text[]` casts | Avoid implicit string interpretation |
| Denormalization consistency | Database triggers | Prevent silent data corruption |
| Vector hash uniqueness | No unique constraint | Enable global embedding cache |
| Function naming | Prefix with `crader_` | Avoid namespace conflicts |
| FTS language config | Use 'simple' | Precise matching for code |
| FTS trigger overhead | Accept ~5x overhead | Simplicity over micro-optimization |

---

## Migration Checklist

Before deploying:

- [ ] Test upgrade on empty database
- [ ] Test downgrade (verify all triggers/functions dropped)
- [ ] Verify triggers fire correctly:
  ```sql
  -- Should fail (mismatched snapshot_id)
  INSERT INTO symbols (file_id, snapshot_id, ...)
  VALUES ('file-in-snap-A', 'snap-B', ...);
  ```
- [ ] Check FTS trigger populates correctly:
  ```sql
  SELECT COUNT(*) FROM code_chunk_fts;
  -- Should match: SELECT COUNT(*) FROM code_chunks;
  ```
- [ ] Measure indexing performance on sample data
- [ ] Document any PG version-specific behavior

---

## Version History

| Version | Date | Changes |
|---------|------|---------|
| 2.0 | 2026-01-25 | Initial v2 schema with design decisions |

---

*This schema documentation is maintained alongside the codebase. For questions or clarifications, see the RFC or open an issue.*
