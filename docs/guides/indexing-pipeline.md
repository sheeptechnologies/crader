# Indexing Pipeline

This guide explains the complete indexing workflow, from source code to queryable knowledge graph.

## Overview

The indexing pipeline transforms raw source code into a structured, searchable knowledge graph through multiple stages:

```
Source Files → Parsing → Chunking → Relation Extraction → Embedding → Storage
```

Each stage is designed for **performance**, **accuracy**, and **scalability**.

---

## Stage 1: Repository Preparation

### Git Volume Management

The `GitVolumeManager` handles repository cloning and updates:

```python
from code_graph_indexer.volume_manager import GitVolumeManager

manager = GitVolumeManager(workspace_dir="./workspace")

# Clone or update repository
manager.ensure_repo_updated(
    repo_url="https://github.com/org/repo",
    branch="main"
)

# Get file list (respects .gitignore)
files = manager.files(repo_path)
```

**Key Features**:
- Shallow clones for faster downloads
- Respects `.gitignore` patterns
- Filters binary files automatically
- Caches repositories for reuse

**Performance**: ~5s for initial clone, ~1s for updates

---

## Stage 2: Tree-sitter Parsing

### AST Generation

Tree-sitter creates an **Abstract Syntax Tree** for each file:

```python
from code_graph_indexer.parsing import TreeSitterRepoParser

parser = TreeSitterRepoParser(repo_path="./repo")

# Parse a single file
chunks, relations = parser.parse_file(
    file_path="src/main.py",
    file_id="file_123"
)
```

### Why Tree-sitter?

| Feature | Benefit |
|---------|---------|
| **Zero-copy** | No string allocations during parsing |
| **Incremental** | Reparse only changed sections |
| **Error-tolerant** | Handles syntax errors gracefully |
| **Multi-language** | Unified API for 40+ languages |

### Supported Languages

- Python
- TypeScript/JavaScript
- Go
- Java
- Rust
- C/C++
- Ruby
- PHP

---

## Stage 3: Semantic Chunking

### Chunking Strategy

Code is split into **semantic chunks** that respect code structure:

```python
# Configuration
CHUNK_SIZE = 512        # Target tokens per chunk
CHUNK_OVERLAP = 50      # Overlap for context
MAX_CHUNK_SIZE = 2048   # Hard limit
```

### Chunking Rules

1. **Respect Boundaries**: Never split mid-function or mid-class
2. **Preserve Context**: Include overlap from previous chunk
3. **Handle Large Nodes**: Recursively split oversized functions
4. **Maintain Metadata**: Track semantic type (function, class, etc.)

### Example

```python
# Original code
def process_data(data):
    """Process incoming data."""
    validated = validate(data)
    transformed = transform(validated)
    return save(transformed)

def validate(data):
    """Validate data structure."""
    # ... implementation
```

**Chunks Created**:

**Chunk 1** (function: `process_data`):
```python
def process_data(data):
    """Process incoming data."""
    validated = validate(data)
    transformed = transform(validated)
    return save(transformed)
```
Metadata: `{type: "function", name: "process_data", calls: ["validate", "transform", "save"]}`

**Chunk 2** (function: `validate`, with overlap):
```python
# Overlap from previous chunk
    return save(transformed)

def validate(data):
    """Validate data structure."""
    # ... implementation
```
Metadata: `{type: "function", name: "validate"}`

### Chunking Algorithm

```python
def _process_scope(node, content, file_path, parent_id):
    """Recursively process AST nodes into chunks."""
    
    # Extract semantic information
    semantic_matches = _extract_tags(node, content)
    
    # Check size
    node_size = node.end_byte - node.start_byte
    
    if node_size <= MAX_CHUNK_SIZE:
        # Create single chunk
        chunk = ChunkNode(
            content=content[node.start_byte:node.end_byte],
            metadata={"semantic_matches": semantic_matches},
            file_path=file_path,
            parent_id=parent_id
        )
        return [chunk]
    else:
        # Recursively split children
        chunks = []
        for child in node.children:
            chunks.extend(_process_scope(child, content, file_path, parent_id))
        return chunks
```

### Metadata Extraction

During parsing, rich metadata is extracted from each chunk:

#### Tree-sitter Queries

Custom queries extract semantic information:

```scheme
; Function definitions
(function_definition
  name: (identifier) @function.name
  parameters: (parameters) @function.params
  return_type: (type)? @function.return
  body: (block) @function.body
  (#set! "type" "function"))

; Class definitions
(class_definition
  name: (identifier) @class.name
  superclasses: (argument_list)? @class.bases
  body: (block) @class.body
  (#set! "type" "class"))

; Method calls
(call
  function: (attribute
    object: (identifier) @call.object
    attribute: (identifier) @call.method))
```

#### Extracted Information

For each chunk, we extract:

**1. Semantic Type**:
```python
{
    "type": "function",  # or "class", "method", "variable"
    "identifier": "process_request",
    "signature": "process_request(data: Dict) -> Response"
}
```

**2. Documentation**:
```python
{
    "docstring": "Process incoming HTTP request and return response.",
    "has_docstring": True,
    "docstring_lines": 3
}
```

**3. Type Information**:
```python
{
    "parameters": [
        {"name": "data", "type": "Dict", "has_default": False},
        {"name": "timeout", "type": "int", "has_default": True, "default": "30"}
    ],
    "return_type": "Response",
    "has_type_hints": True
}
```

**4. Relationships**:
```python
{
    "calls": ["validate_data", "transform_response"],
    "imports": ["typing.Dict", "models.Response"],
    "decorators": ["@app.route", "@require_auth"],
    "raises": ["ValueError", "TimeoutError"]
}
```

**5. Code Patterns**:
```python
{
    "is_async": False,
    "is_generator": False,
    "is_property": False,
    "is_static": False,
    "is_classmethod": False,
    "uses_context_manager": True,
    "has_error_handling": True
}
```

**6. Complexity Metrics**:
```python
{
    "lines_of_code": 25,
    "cyclomatic_complexity": 4,
    "nesting_depth": 2,
    "num_parameters": 2,
    "num_return_statements": 3
}
```

#### Complete Metadata Example

```python
{
    # Identity
    "id": "node_abc123",
    "chunk_id": "chunk_def456",
    "file_id": "file_789",
    
    # Location
    "file_path": "src/api/handlers.py",
    "start_line": 45,
    "end_line": 70,
    "start_byte": 1200,
    "end_byte": 1850,
    
    # Content
    "content_hash": "sha256:abc...",
    "language": "python",
    "size_bytes": 650,
    
    # Semantic Information
    "semantic_matches": [
        {
            "type": "function",
            "identifier": "process_request",
            "signature": "process_request(data: Dict, timeout: int = 30) -> Response",
            "docstring": "Process incoming HTTP request and return response.",
            "parameters": [
                {"name": "data", "type": "Dict"},
                {"name": "timeout", "type": "int", "default": "30"}
            ],
            "return_type": "Response",
            "decorators": ["@app.route('/api/process')", "@require_auth"],
            "is_async": False
        }
    ],
    
    # Relationships
    "calls": [
        {"name": "validate_data", "line": 48},
        {"name": "transform_response", "line": 65}
    ],
    "imports": [
        {"module": "typing", "name": "Dict"},
        {"module": "models", "name": "Response"}
    ],
    "raises": ["ValueError", "TimeoutError"],
    
    # Code Quality
    "has_docstring": True,
    "has_type_hints": True,
    "has_error_handling": True,
    "cyclomatic_complexity": 4,
    
    # Git Metadata (from MetadataProvider)
    "git_metadata": {
        "last_modified": "2024-01-15T10:30:00Z",
        "author": "john.doe@example.com",
        "commit_hash": "abc123",
        "file_category": "code"  # or "test", "config", "docs"
    },
    
    # Tags
    "tags": [
        "function_definition",
        "has_decorators",
        "has_type_hints",
        "api_endpoint",
        "requires_auth"
    ]
}
```

This rich metadata enables:
- **Precise Search**: Find functions by signature, decorators, or patterns
- **Quality Analysis**: Filter by code quality metrics
- **Impact Analysis**: Understand relationships and dependencies
- **Context-Aware Retrieval**: Provide LLMs with comprehensive information

---

## Stage 4: SCIP Relation Extraction

### What is SCIP?

**SCIP** (SCIP Code Intelligence Protocol) is an industry-standard format for representing code relationships.

### Relation Types

| Relation | Description | Example |
|----------|-------------|---------|
| **Definition** | Where a symbol is defined | `def foo():` |
| **Reference** | Where a symbol is used | `result = foo()` |
| **Call** | Function invocation | `foo()` |
| **Inheritance** | Class hierarchy | `class Child(Parent):` |
| **Import** | Module dependencies | `from x import y` |

### SCIP Indexing Process

```python
from code_graph_indexer.graph.indexers.scip import SCIPRunner

runner = SCIPRunner(repo_path="./repo")

# Generate SCIP index
indices = runner.prepare_indices()

# Stream documents
for doc_wrapper in runner.stream_documents(indices):
    # Process definitions
    definitions = extract_definitions(doc_wrapper)
    
    # Process references
    references = extract_references(doc_wrapper)
    
    # Store in graph
    storage.ingest_scip_relations(definitions, references)
```

### Performance Optimization

- **Streaming**: Process large indices without loading into memory
- **Batching**: Bulk insert relations using COPY protocol
- **Parallel**: Run SCIP indexing concurrently with Tree-sitter parsing

**Performance**: ~100K relations/second on modern hardware

---

## Stage 5: Embedding Generation

### Embedding Pipeline

```python
from code_graph_indexer.providers.embedding import CodeEmbedder

embedder = CodeEmbedder(
    storage=storage,
    provider=embedding_provider,
    batch_size=100,
    max_concurrency=4
)

# Generate embeddings for new chunks
async for update in embedder.embed_all(snapshot_id):
    print(f"Embedded {update.processed}/{update.total} chunks")
```

### Staging Strategy

Embeddings are generated in a **staging pipeline**:

1. **Fetch**: Get chunks without embeddings
2. **Hash**: Compute content hash to detect duplicates
3. **Batch**: Group chunks for efficient API calls
4. **Embed**: Call embedding provider (OpenAI, Cohere, local)
5. **Store**: Save vectors to database

### Deduplication

Chunks with identical content share embeddings:

```python
# Hash-based deduplication
chunk_hash = hashlib.sha256(content.encode()).hexdigest()

# Check if embedding exists
existing = storage.get_embedding_by_hash(chunk_hash)

if existing:
    # Reuse existing embedding
    storage.link_embedding(chunk_id, existing.vector_hash)
else:
    # Generate new embedding
    vector = await provider.embed(content)
    storage.save_embedding(chunk_id, vector, chunk_hash)
```

**Benefit**: 30-50% reduction in embedding API calls for typical codebases

---

## Stage 6: Storage

### Database Schema

```sql
-- Snapshots: Atomic versions
CREATE TABLE snapshots (
    id UUID PRIMARY KEY,
    repository_id UUID,
    commit_hash TEXT,
    status TEXT,
    created_at TIMESTAMP
);

-- Nodes: Code chunks
CREATE TABLE nodes (
    id UUID PRIMARY KEY,
    snapshot_id UUID,
    file_path TEXT,
    content_hash TEXT,
    start_line INT,
    end_line INT,
    metadata JSONB
);

-- Edges: Relationships
CREATE TABLE edges (
    source_id UUID,
    target_id UUID,
    relation_type TEXT,
    metadata JSONB
);

-- Embeddings: Vectors
CREATE TABLE node_embeddings (
    node_id UUID,
    vector_hash TEXT,
    embedding vector(1536),
    model_name TEXT
);
```

### COPY Protocol

Bulk inserts use PostgreSQL's COPY protocol for maximum performance:

```python
def add_nodes_fast(nodes: List[ChunkNode]):
    """Insert nodes using COPY protocol (10x-50x faster)."""
    
    sql = """
        COPY nodes (id, file_path, content_hash, start_line, end_line, metadata)
        FROM STDIN
    """
    
    with conn.cursor() as cur:
        with cur.copy(sql) as copy:
            for node in nodes:
                copy.write_row((
                    node.id,
                    node.file_path,
                    node.content_hash,
                    node.start_line,
                    node.end_line,
                    json.dumps(node.metadata)
                ))
```

**Performance**: ~100K nodes/second vs ~2K nodes/second with INSERT

---

## Complete Workflow

### Indexing a Repository

```python
from code_graph_indexer import CodebaseIndexer

indexer = CodebaseIndexer(
    repo_path="./repo",
    storage_connector=connector,
    max_workers=8  # Parallel processing
)

# Full indexing workflow
indexer.index(
    repo_url="https://github.com/org/repo",
    branch="main"
)
```

### Internal Steps

1. **Create Snapshot**: Atomic version for this index
2. **Clone/Update**: Ensure repository is current
3. **Parse Files**: Tree-sitter + SCIP in parallel
4. **Store Graph**: Bulk insert nodes and edges
5. **Generate Embeddings**: Async batch processing
6. **Activate Snapshot**: Atomic swap to new version

### Progress Tracking

```python
# Monitor indexing progress
for progress in indexer.index_with_progress(repo_url, branch):
    print(f"Stage: {progress.stage}")
    print(f"Progress: {progress.current}/{progress.total}")
    print(f"ETA: {progress.eta}")
```

---

## Performance Tuning

### Parallel Processing

```python
# Adjust based on CPU cores
indexer = CodebaseIndexer(
    max_workers=os.cpu_count()  # Default: 8
)
```

### Batch Sizes

```python
# Larger batches = fewer API calls, more memory
embedder = CodeEmbedder(
    batch_size=200,  # Default: 100
    max_concurrency=8  # Default: 4
)
```

### Database Tuning

```sql
-- Increase work memory for bulk operations
SET work_mem = '256MB';

-- Disable auto-vacuum during indexing
ALTER TABLE nodes SET (autovacuum_enabled = false);

-- Re-enable after indexing
ALTER TABLE nodes SET (autovacuum_enabled = true);
VACUUM ANALYZE nodes;
```

---

## Incremental Updates

### Detecting Changes

```python
# Only reindex changed files
changed_files = indexer.detect_changes(
    old_commit="abc123",
    new_commit="def456"
)

# Reindex only changed files
indexer.reindex_files(changed_files, snapshot_id)
```

### Snapshot Strategy

- **Full Reindex**: Create new snapshot, index all files
- **Incremental**: Reuse existing snapshot, update changed files
- **Atomic Swap**: Switch to new snapshot when complete

**Benefit**: 10x-100x faster for small changes

---

## Monitoring

### Metrics to Track

```python
# Indexing stats
stats = indexer.get_stats()
print(f"Files indexed: {stats['files_indexed']}")
print(f"Nodes created: {stats['total_nodes']}")
print(f"Edges created: {stats['total_edges']}")
print(f"Embeddings generated: {stats['embeddings_count']}")
print(f"Duration: {stats['duration_seconds']}s")
```

### Common Issues

| Issue | Cause | Solution |
|-------|-------|----------|
| Slow parsing | Large files | Increase `max_workers` |
| OOM errors | Large batches | Reduce `batch_size` |
| Slow embeddings | API rate limits | Reduce `max_concurrency` |
| Slow inserts | Not using COPY | Enable `use_copy_protocol=True` |

---

## Next Steps

- [Embedding Strategy](embedding-strategy.md): Deep dive into vector generation
- [Retrieval Strategies](retrieval-strategies.md): Using the indexed graph
- [API Reference](../reference/indexer.md): Complete API documentation
