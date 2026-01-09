# Embedding Strategy

This guide explains how code chunks are converted into semantic vectors for similarity search.

## Overview

Embeddings transform code into high-dimensional vectors that capture semantic meaning. Similar code produces similar vectors, enabling semantic search beyond keyword matching.

```
Code Chunk → Tokenization → Embedding Model → Vector (1536 dims) → Storage
```

---

## Why Embeddings for Code?

### Traditional Search Limitations

**Keyword search** fails when:
- Variable names differ (`process_data` vs `handle_request`)
- Comments are missing
- Code is refactored but semantically identical

**Example**:
```python
# Version 1
def calculate_total(items):
    return sum(item.price for item in items)

# Version 2 (semantically identical)
def compute_sum(products):
    total = 0
    for product in products:
        total += product.cost
    return total
```

Keyword search sees these as completely different. **Embeddings** recognize semantic similarity.

---

## Embedding Models

### Provider Options

The library supports multiple embedding providers:

**Cloud Providers**:
- **OpenAI**: Industry-standard models with good performance
- **Cohere**: Optimized for semantic search
- **Anthropic**: High-quality embeddings
- **Google**: Vertex AI embedding models

**Local Models**:
- **Sentence Transformers**: Free, runs locally
- **ONNX Runtime**: Optimized inference
- **Custom Models**: Bring your own embedding model

### Choosing a Model

**For production**:
- Cloud providers offer best accuracy and reliability
- Consider API costs vs infrastructure costs
- Evaluate based on your specific use case

**For development**:
- Local models avoid API costs
- Good for testing and prototyping
- Switch to cloud providers for production

### Configuration

```python
from code_graph_indexer.providers.embedding import OpenAIEmbeddingProvider

provider = OpenAIEmbeddingProvider(
    model="text-embedding-3-small",
    api_key=os.getenv("OPENAI_API_KEY"),
    max_retries=3,
    timeout=30
)
```

**Note**: Model availability and capabilities change frequently. Check provider documentation for current offerings.

---

## What Gets Embedded

Understanding exactly what content is embedded is crucial for optimizing search quality and relevance.

### Embedding Template

Each code chunk is embedded using a structured template that includes:

```
File: {file_path}
Language: {language}
Type: {semantic_type}

{code_content}

Context:
- Defined symbols: {symbols}
- Calls: {function_calls}
- Imports: {imports}
```

### Example: Function Chunk

**Original Code**:
```python
# src/auth/validators.py
import re
from typing import Optional

def validate_email(email: str) -> bool:
    """Validate email format using regex."""
    pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    return re.match(pattern, email) is not None
```

**Embedded Content**:
```
File: src/auth/validators.py
Language: python
Type: function
Name: validate_email

import re
from typing import Optional

def validate_email(email: str) -> bool:
    """Validate email format using regex."""
    pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    return re.match(pattern, email) is not None

Context:
- Defined symbols: validate_email
- Calls: re.match
- Imports: re, typing.Optional
- Parameters: email: str
- Returns: bool
```

### Metadata Included

Each chunk stores comprehensive metadata:

```python
{
    # Identity
    "id": "chunk_uuid_123",
    "file_path": "src/auth/validators.py",
    "file_id": "file_uuid_456",
    
    # Location
    "start_line": 5,
    "end_line": 10,
    "start_byte": 120,
    "end_byte": 350,
    
    # Content
    "content_hash": "sha256_hash",
    "language": "python",
    
    # Semantic Information
    "semantic_matches": [
        {
            "type": "function",
            "identifier": "validate_email",
            "signature": "validate_email(email: str) -> bool",
            "docstring": "Validate email format using regex.",
            "parameters": [
                {"name": "email", "type": "str"}
            ],
            "return_type": "bool"
        }
    ],
    
    # Relationships (from SCIP)
    "definitions": [
        {
            "symbol": "validate_email",
            "kind": "function",
            "range": [5, 0, 5, 14]
        }
    ],
    
    "references": [
        {
            "symbol": "re.match",
            "kind": "function_call",
            "range": [8, 11, 8, 19]
        }
    ],
    
    # Git Metadata
    "git_metadata": {
        "last_modified": "2024-01-15T10:30:00Z",
        "author": "john.doe@example.com",
        "commit_hash": "abc123def456",
        "blame_info": {
            "line_5": {"author": "john.doe", "date": "2024-01-15"}
        }
    },
    
    # Tags (from Tree-sitter queries)
    "tags": [
        "function_definition",
        "has_docstring",
        "has_type_hints",
        "uses_regex"
    ]
}
```

### Class Chunk Example

**Original Code**:
```python
# src/auth/handlers.py
from .validators import validate_email

class AuthHandler:
    """Handle authentication operations."""
    
    def __init__(self, db_connection):
        self.db = db_connection
    
    def register_user(self, email: str, password: str):
        """Register a new user."""
        if not validate_email(email):
            raise ValueError("Invalid email")
        # ... registration logic
```

**Embedded Content**:
```
File: src/auth/handlers.py
Language: python
Type: class
Name: AuthHandler

from .validators import validate_email

class AuthHandler:
    """Handle authentication operations."""
    
    def __init__(self, db_connection):
        self.db = db_connection
    
    def register_user(self, email: str, password: str):
        """Register a new user."""
        if not validate_email(email):
            raise ValueError("Invalid email")

Context:
- Defined symbols: AuthHandler, __init__, register_user
- Calls: validate_email
- Imports: .validators.validate_email
- Methods: __init__, register_user
- Inherits from: (none)
```

### Relationship Graph

Chunks are connected via edges in the graph:

```python
# Edge types stored in database
{
    "source_id": "chunk_auth_handler",
    "target_id": "chunk_validate_email",
    "relation_type": "calls",
    "metadata": {
        "call_site": "src/auth/handlers.py:12",
        "context": "register_user method"
    }
}

{
    "source_id": "chunk_auth_handler",
    "target_id": "file_validators",
    "relation_type": "imports",
    "metadata": {
        "import_statement": "from .validators import validate_email"
    }
}
```

### Content Enrichment

The embedding content is enriched with:

1. **File Context**: Path, language, module structure
2. **Semantic Type**: Function, class, method, variable
3. **Symbol Information**: Names, signatures, types
4. **Relationships**: Calls, imports, inheritance
5. **Documentation**: Docstrings, comments
6. **Git History**: Author, last modified, blame info
7. **Code Patterns**: Detected patterns (async, decorators, etc.)

### Why This Matters

**Better Search Results**:
- Queries like "email validation" match both the function name and its purpose
- Type information helps find functions with specific signatures
- Relationship data enables "find all callers" queries

**Context-Aware Retrieval**:
- LLMs receive not just code, but understanding of what it does
- Import statements help understand dependencies
- Docstrings provide natural language descriptions

**Graph Traversal**:
- Edges enable finding related code
- Call graphs show execution flow
- Import graphs show dependencies

### Customization

You can customize what gets embedded:

```python
from code_graph_indexer.providers.metadata import MetadataProvider

# Custom metadata provider
class CustomMetadataProvider(MetadataProvider):
    def enrich_chunk(self, chunk, file_content):
        """Add custom metadata to chunks."""
        metadata = super().enrich_chunk(chunk, file_content)
        
        # Add custom fields
        metadata["complexity"] = self.calculate_complexity(chunk)
        metadata["test_coverage"] = self.get_coverage(chunk)
        metadata["security_annotations"] = self.extract_security_info(chunk)
        
        return metadata

# Use custom provider
indexer = CodebaseIndexer(
    repo_path="./repo",
    metadata_provider=CustomMetadataProvider()
)
```

---

## Staging Pipeline

### Architecture

Embeddings are generated through a **staging pipeline** to optimize performance and cost:

```
┌─────────────┐
│   Chunks    │
│ (unembedded)│
└──────┬──────┘
       │
       ▼
┌─────────────┐
│   Staging   │  ← Fetch chunks without embeddings
│   Table     │
└──────┬──────┘
       │
       ▼
┌─────────────┐
│ Hash Check  │  ← Detect duplicates
└──────┬──────┘
       │
       ├─ Duplicate? → Reuse existing embedding
       │
       ▼
┌─────────────┐
│   Batch     │  ← Group for API efficiency
└──────┬──────┘
       │
       ▼
┌─────────────┐
│ Embed API   │  ← Call embedding provider
└──────┬──────┘
       │
       ▼
┌─────────────┐
│   Storage   │  ← Save vectors
└─────────────┘
```

### Implementation

```python
class CodeEmbedder:
    async def embed_all(self, snapshot_id: str):
        """Generate embeddings for all chunks in a snapshot."""
        
        while True:
            # Fetch batch of unembedded chunks
            batch = self.storage.fetch_staging_delta(
                snapshot_id=snapshot_id,
                limit=self.batch_size
            )
            
            if not batch:
                break  # All chunks embedded
            
            # Hash-based deduplication
            unique_chunks = self._deduplicate(batch)
            
            # Generate embeddings
            vectors = await self.provider.embed_async(
                texts=[chunk.content for chunk in unique_chunks]
            )
            
            # Save to database
            self.storage.save_embeddings_direct(
                chunks=unique_chunks,
                vectors=vectors,
                model_name=self.provider.model_name
            )
```

---

## Deduplication Strategy

### Content Hashing

Identical code chunks share embeddings:

```python
import hashlib

def compute_hash(content: str) -> str:
    """Compute SHA-256 hash of content."""
    return hashlib.sha256(content.encode()).hexdigest()

# Check if embedding exists
chunk_hash = compute_hash(chunk.content)
existing = storage.get_embedding_by_hash(chunk_hash)

if existing:
    # Reuse existing embedding
    storage.link_embedding(chunk.id, existing.vector_hash)
else:
    # Generate new embedding
    vector = await provider.embed(chunk.content)
    storage.save_embedding(chunk.id, vector, chunk_hash)
```

### Benefits

- **Cost Reduction**: 30-50% fewer API calls
- **Faster Indexing**: Skip redundant embeddings
- **Consistency**: Identical code always has identical vectors

### Example

```python
# File 1: utils.py
def validate_email(email):
    return "@" in email

# File 2: helpers.py (copied)
def validate_email(email):
    return "@" in email
```

Both chunks get the **same embedding** (only computed once).

---

## Batch Processing

### Why Batching?

Embedding APIs have:
- **Rate limits**: Max requests/minute
- **Latency**: Network overhead per request
- **Cost**: Charged per token

**Batching** reduces overhead:
- 1 request for 100 chunks vs 100 requests
- Lower latency (parallel processing)
- Better throughput

### Configuration

```python
embedder = CodeEmbedder(
    storage=storage,
    provider=provider,
    batch_size=100,        # Chunks per API call
    max_concurrency=4      # Parallel requests
)
```

### Optimal Batch Size

| Repository Size | Batch Size | Concurrency | Throughput |
|----------------|------------|-------------|------------|
| Small (<1K chunks) | 50 | 2 | ~500 chunks/min |
| Medium (1K-10K) | 100 | 4 | ~2K chunks/min |
| Large (>10K) | 200 | 8 | ~5K chunks/min |

**Rule of thumb**: Larger batches = better throughput, but more memory

---

## Async Processing

### Concurrent Embedding

```python
import asyncio

async def embed_concurrent(chunks, provider, max_concurrency=4):
    """Embed chunks with controlled concurrency."""
    
    semaphore = asyncio.Semaphore(max_concurrency)
    
    async def embed_with_limit(chunk):
        async with semaphore:
            return await provider.embed_async(chunk.content)
    
    # Process all chunks concurrently
    tasks = [embed_with_limit(chunk) for chunk in chunks]
    vectors = await asyncio.gather(*tasks)
    
    return vectors
```

### Rate Limiting

```python
from aiolimiter import AsyncLimiter

class RateLimitedProvider:
    def __init__(self, provider, max_rate=100):
        self.provider = provider
        self.limiter = AsyncLimiter(max_rate, 60)  # max_rate per minute
    
    async def embed_async(self, text):
        async with self.limiter:
            return await self.provider.embed_async(text)
```

---

## Vector Storage

### Database Schema

```sql
CREATE TABLE node_embeddings (
    node_id UUID PRIMARY KEY,
    vector_hash TEXT NOT NULL,
    embedding vector(1536),  -- pgvector type
    model_name TEXT,
    created_at TIMESTAMP DEFAULT NOW()
);

-- Index for vector similarity search
CREATE INDEX idx_embeddings_vector 
ON node_embeddings 
USING ivfflat (embedding vector_cosine_ops)
WITH (lists = 100);
```

### Indexing Strategy

**IVFFlat** (Inverted File with Flat compression):
- Partitions vectors into clusters
- Fast approximate search
- Good for 10K-1M vectors

**HNSW** (Hierarchical Navigable Small World):
- Graph-based index
- Faster queries, slower inserts
- Good for >1M vectors

```sql
-- For large datasets (>1M vectors)
CREATE INDEX idx_embeddings_hnsw 
ON node_embeddings 
USING hnsw (embedding vector_cosine_ops)
WITH (m = 16, ef_construction = 64);
```

---

## Incremental Updates

### Detecting Changes

```python
def get_changed_chunks(old_snapshot, new_snapshot):
    """Find chunks that need re-embedding."""
    
    # Get all chunks in new snapshot
    new_chunks = storage.get_chunks(new_snapshot)
    
    # Check which ones lack embeddings
    unembedded = []
    for chunk in new_chunks:
        if not storage.has_embedding(chunk.id):
            unembedded.append(chunk)
    
    return unembedded
```

### Reusing Embeddings

```python
def reuse_embeddings(old_snapshot, new_snapshot):
    """Copy embeddings for unchanged chunks."""
    
    # Find chunks with identical content
    for old_chunk in storage.get_chunks(old_snapshot):
        new_chunk = storage.find_chunk_by_hash(
            new_snapshot,
            old_chunk.content_hash
        )
        
        if new_chunk:
            # Copy embedding reference
            storage.copy_embedding(
                from_chunk=old_chunk.id,
                to_chunk=new_chunk.id
            )
```

**Benefit**: Only embed new/changed code (90%+ reuse for typical changes)

---

## Performance Optimization

### Memory Management

```python
# Process in chunks to avoid OOM
async def embed_large_dataset(chunks, batch_size=1000):
    for i in range(0, len(chunks), batch_size):
        batch = chunks[i:i+batch_size]
        await embed_batch(batch)
        
        # Free memory
        del batch
        gc.collect()
```

### Caching

```python
from functools import lru_cache

@lru_cache(maxsize=10000)
def get_embedding_cached(chunk_hash):
    """Cache frequently accessed embeddings."""
    return storage.get_embedding_by_hash(chunk_hash)
```

### Monitoring

```python
# Track embedding progress
stats = {
    "total_chunks": 0,
    "embedded": 0,
    "reused": 0,
    "api_calls": 0,
    "tokens_used": 0,
    "cost_usd": 0.0
}

async for update in embedder.embed_all(snapshot_id):
    stats["embedded"] = update.processed
    stats["total_chunks"] = update.total
    
    print(f"Progress: {update.processed}/{update.total}")
    print(f"Reused: {stats['reused']} ({stats['reused']/update.total*100:.1f}%)")
    print(f"Cost: ${stats['cost_usd']:.2f}")
```

---

## Cost Optimization

### Strategies

1. **Deduplication**: Reuse embeddings for identical code (30-50% savings)
2. **Incremental Updates**: Only embed changed chunks (90%+ savings on updates)
3. **Batch Processing**: Reduce API overhead and improve throughput
4. **Local Models**: Consider for development and testing environments
5. **Model Selection**: Balance accuracy vs cost for your use case

### Best Practices

**Minimize API Calls**:
- Enable content-based deduplication
- Use incremental indexing for updates
- Batch requests efficiently
- Cache embeddings aggressively

**Optimize Infrastructure**:
- Use local models for non-production environments
- Implement request caching
- Monitor usage and set budgets
- Consider reserved capacity for predictable workloads

**Monitor Costs**:
```python
# Track embedding usage
stats = {
    "total_chunks": 0,
    "embedded": 0,
    "reused": 0,
    "api_calls": 0
}

async for update in embedder.embed_all(snapshot_id):
    stats["embedded"] = update.processed
    stats["total_chunks"] = update.total
    
    print(f"Progress: {update.processed}/{update.total}")
    print(f"Reused: {stats['reused']} ({stats['reused']/update.total*100:.1f}%)")
```

---

## Error Handling

### Retry Logic

```python
from tenacity import retry, stop_after_attempt, wait_exponential

@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=4, max=10)
)
async def embed_with_retry(text, provider):
    """Embed with automatic retries."""
    try:
        return await provider.embed_async(text)
    except Exception as e:
        print(f"Embedding failed: {e}")
        raise
```

### Partial Failures

```python
async def embed_batch_safe(chunks, provider):
    """Embed batch with individual error handling."""
    
    results = []
    for chunk in chunks:
        try:
            vector = await provider.embed_async(chunk.content)
            results.append((chunk.id, vector, None))
        except Exception as e:
            # Log error but continue
            results.append((chunk.id, None, str(e)))
    
    return results
```

---

## Testing

### Unit Tests

```python
def test_deduplication():
    """Test that identical chunks share embeddings."""
    
    chunk1 = ChunkNode(content="def foo(): pass")
    chunk2 = ChunkNode(content="def foo(): pass")
    
    embedder.embed([chunk1, chunk2])
    
    # Should only call API once
    assert provider.embed_async.call_count == 1
    
    # Should have same embedding
    emb1 = storage.get_embedding(chunk1.id)
    emb2 = storage.get_embedding(chunk2.id)
    assert emb1.vector_hash == emb2.vector_hash
```

### Integration Tests

```python
async def test_full_embedding_pipeline():
    """Test complete embedding workflow."""
    
    # Index repository
    indexer.index(repo_url, branch)
    
    # Generate embeddings
    await embedder.embed_all(snapshot_id)
    
    # Verify all chunks have embeddings
    chunks = storage.get_chunks(snapshot_id)
    for chunk in chunks:
        assert storage.has_embedding(chunk.id)
```

---

## Next Steps

- [Retrieval Strategies](retrieval-strategies.md): Using embeddings for search
- [API Reference](../reference/embedding.md): Complete embedding API
- [Production Deployment](../deployment/production.md): Scaling embedding generation
