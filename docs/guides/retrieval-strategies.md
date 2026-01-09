# Retrieval Strategies

This guide explains how to query the indexed code graph using hybrid search, graph traversal, and context expansion.

## Overview

The retrieval system combines three complementary approaches:

1. **Vector Search**: Semantic similarity using embeddings
2. **Full-Text Search**: Keyword matching with PostgreSQL FTS
3. **Graph Traversal**: Navigate relationships (calls, references, inheritance)

These are combined using **Reciprocal Rank Fusion (RRF)** for optimal results.

---

## Architecture

```
Query → Multi-Stage Pipeline → Results

Stage 1: Resolution
  ├─ Parse query intent
  ├─ Extract keywords
  └─ Identify target types (function, class, etc.)

Stage 2: Search
  ├─ Vector Search (semantic)
  ├─ Full-Text Search (keywords)
  └─ RRF Fusion

Stage 3: Expansion
  ├─ Graph Traversal
  ├─ Context Gathering
  └─ Ranking
```

---

## Basic Usage

### Simple Search

```python
from code_graph_indexer import CodeRetriever

retriever = CodeRetriever(connector)

# Search with default settings
results = retriever.search(
    query="authentication middleware implementation",
    limit=10
)

for result in results:
    print(f"{result['file_path']}:{result['start_line']}")
    print(result['content'])
    print(f"Score: {result['score']}\n")
```

### Hybrid Search

```python
# Combine vector + keyword search
results = retriever.search(
    query="JWT token validation",
    limit=10,
    search_mode="hybrid",  # "vector", "keyword", or "hybrid"
    rrf_k=60  # RRF parameter (default: 60)
)
```

---

## Vector Search

### How It Works

1. **Query Embedding**: Convert query to vector
2. **Similarity Search**: Find nearest neighbors using cosine similarity
3. **Ranking**: Order by similarity score

```python
# Pure vector search
results = retriever.search(
    query="handle user authentication",
    search_mode="vector",
    limit=20
)
```

### Similarity Metrics

| Metric | Formula | Use Case |
|--------|---------|----------|
| **Cosine** | `1 - (A·B)/(‖A‖‖B‖)` | Default, best for code |
| **L2 Distance** | `‖A-B‖²` | Absolute distance |
| **Inner Product** | `A·B` | Normalized vectors |

**Default**: Cosine similarity (best for semantic code search)

### Performance

```sql
-- Vector index for fast search
CREATE INDEX idx_embeddings_vector 
ON node_embeddings 
USING ivfflat (embedding vector_cosine_ops)
WITH (lists = 100);

-- Query performance
EXPLAIN ANALYZE
SELECT node_id, embedding <=> query_vector AS distance
FROM node_embeddings
ORDER BY distance
LIMIT 10;
```

**Typical performance**: <50ms for 100K vectors

---

## Full-Text Search

### PostgreSQL FTS

Uses PostgreSQL's built-in full-text search with custom configurations:

```sql
-- Create FTS index
CREATE INDEX idx_contents_fts 
ON contents 
USING gin(to_tsvector('english', content));

-- Search query
SELECT node_id, ts_rank(to_tsvector('english', content), query) AS rank
FROM contents
WHERE to_tsvector('english', content) @@ plainto_tsquery('english', 'authentication JWT')
ORDER BY rank DESC;
```

### Advanced FTS

```python
# Keyword search with filters
results = retriever.search(
    query="validate email",
    search_mode="keyword",
    filters={
        "file_path": "src/auth/",  # Path prefix
        "language": "python",
        "type": "function"
    }
)
```

---

## Reciprocal Rank Fusion (RRF)

### Algorithm

RRF combines rankings from multiple sources:

```
RRF_score(d) = Σ 1 / (k + rank_i(d))
```

Where:
- `d` = document
- `k` = constant (default: 60)
- `rank_i(d)` = rank of document in source i

### Example

**Vector Search Results**:
1. doc_A (rank 1)
2. doc_B (rank 2)
3. doc_C (rank 3)

**Keyword Search Results**:
1. doc_B (rank 1)
2. doc_C (rank 2)
3. doc_D (rank 3)

**RRF Scores** (k=60):
- doc_A: 1/(60+1) = 0.0164
- doc_B: 1/(60+1) + 1/(60+1) = 0.0328 ← **Best**
- doc_C: 1/(60+3) + 1/(60+2) = 0.0317
- doc_D: 1/(60+3) = 0.0159

**Final Ranking**: B, C, A, D

### Implementation

```python
def reciprocal_rank_fusion(rankings: List[List[str]], k: int = 60) -> List[str]:
    """Combine multiple rankings using RRF."""
    scores = {}
    
    for ranking in rankings:
        for rank, doc_id in enumerate(ranking, start=1):
            scores[doc_id] = scores.get(doc_id, 0) + 1 / (k + rank)
    
    # Sort by score descending
    return sorted(scores.keys(), key=lambda d: scores[d], reverse=True)
```

---

## Graph Traversal

### Navigation Patterns

```python
from code_graph_indexer import CodeNavigator

navigator = CodeNavigator(connector)

# Find all callers of a function
callers = navigator.get_incoming_calls(node_id="func_123")

# Find all functions called by this function
callees = navigator.get_outgoing_calls(node_id="func_123")

# Get class hierarchy
hierarchy = navigator.get_class_hierarchy(class_name="BaseHandler")

# Find all implementations of an interface
implementations = navigator.find_implementations("IHandler")
```

### Context Expansion

Automatically include related code:

```python
# Search with context expansion
results = retriever.search(
    query="authentication flow",
    limit=10,
    include_context=True,
    context_depth=2  # How many hops in the graph
)

# Each result includes:
# - The matched chunk
# - Callers (who calls this)
# - Callees (what this calls)
# - Definitions (symbols used)
# - Related chunks (same file/class)
```

### Graph Queries

```python
# Custom graph traversal
def find_all_dependencies(node_id: str, max_depth: int = 3):
    """Find all transitive dependencies."""
    visited = set()
    queue = [(node_id, 0)]
    dependencies = []
    
    while queue:
        current, depth = queue.pop(0)
        if current in visited or depth > max_depth:
            continue
        
        visited.add(current)
        
        # Get outgoing calls
        callees = navigator.get_outgoing_calls(current)
        dependencies.extend(callees)
        
        # Add to queue
        for callee in callees:
            queue.append((callee['id'], depth + 1))
    
    return dependencies
```

---

## Multi-Stage Retrieval

### Complete Pipeline

```python
class SearchExecutor:
    def search(self, query: str, limit: int = 10):
        """Multi-stage retrieval pipeline."""
        
        # Stage 1: Resolution
        intent = self._parse_query(query)
        
        # Stage 2: Search
        vector_results = self._vector_search(query, limit * 2)
        keyword_results = self._keyword_search(query, limit * 2)
        
        # Combine with RRF
        combined = self._rrf_fusion([vector_results, keyword_results])
        
        # Stage 3: Expansion
        expanded = self._expand_context(combined[:limit])
        
        # Stage 4: Ranking
        final = self._rerank(expanded, query)
        
        return final[:limit]
```

### Query Intent Detection

```python
def _parse_query(self, query: str) -> Dict:
    """Extract intent from query."""
    
    intent = {
        "type": None,  # function, class, variable
        "action": None,  # find, implement, debug
        "keywords": [],
        "filters": {}
    }
    
    # Detect type
    if "function" in query.lower() or "def " in query:
        intent["type"] = "function"
    elif "class" in query.lower():
        intent["type"] = "class"
    
    # Detect action
    if any(word in query.lower() for word in ["implement", "create", "build"]):
        intent["action"] = "implement"
    elif any(word in query.lower() for word in ["find", "search", "locate"]):
        intent["action"] = "find"
    
    # Extract keywords
    intent["keywords"] = self._extract_keywords(query)
    
    return intent
```

---

## Advanced Filtering

### Path Filters

```python
# Search within specific directories
results = retriever.search(
    query="database connection",
    filters={
        "path_prefix": "src/db/",
        "exclude_patterns": ["**/test_*.py", "**/__pycache__"]
    }
)
```

### Metadata Filters

```python
# Filter by semantic type
results = retriever.search(
    query="validation logic",
    filters={
        "semantic_type": "function",
        "has_docstring": True,
        "min_lines": 10,
        "max_lines": 100
    }
)
```

### Language Filters

```python
# Search only Python files
results = retriever.search(
    query="async request handler",
    filters={"language": "python"}
)
```

---

## Performance Optimization

### Caching

```python
from functools import lru_cache

@lru_cache(maxsize=1000)
def search_cached(query: str, limit: int):
    """Cache frequent queries."""
    return retriever.search(query, limit)
```

### Batch Queries

```python
# Search multiple queries efficiently
queries = [
    "authentication flow",
    "database connection",
    "error handling"
]

results = retriever.search_batch(queries, limit=10)
```

### Index Optimization

```sql
-- Optimize vector index
VACUUM ANALYZE node_embeddings;

-- Rebuild FTS index
REINDEX INDEX idx_contents_fts;

-- Update statistics
ANALYZE contents;
```

---

## Monitoring

### Query Performance

```python
import time

def search_with_metrics(query: str):
    """Track search performance."""
    start = time.time()
    
    results = retriever.search(query, limit=10)
    
    duration = time.time() - start
    
    print(f"Query: {query}")
    print(f"Results: {len(results)}")
    print(f"Duration: {duration*1000:.2f}ms")
    
    return results
```

### Common Metrics

| Metric | Target | Description |
|--------|--------|-------------|
| **Latency** | <100ms | Time to return results |
| **Precision@10** | >0.8 | Relevant results in top 10 |
| **Recall@10** | >0.6 | Found relevant results |
| **Cache Hit Rate** | >70% | Cached query reuse |

---

## Best Practices

### 1. Use Hybrid Search

Combine vector + keyword for best results:
```python
results = retriever.search(query, search_mode="hybrid")
```

### 2. Enable Context Expansion

Include related code for better understanding:
```python
results = retriever.search(query, include_context=True)
```

### 3. Filter Aggressively

Narrow search scope for faster results:
```python
results = retriever.search(
    query,
    filters={"path_prefix": "src/", "language": "python"}
)
```

### 4. Tune RRF Parameter

Adjust `k` based on your needs:
- Lower k (30-40): Favor top-ranked results
- Higher k (80-100): More balanced fusion

### 5. Monitor Performance

Track query latency and optimize indexes.

---

## Troubleshooting

### Slow Queries

```sql
-- Check index usage
EXPLAIN ANALYZE
SELECT * FROM node_embeddings
WHERE embedding <=> query_vector
LIMIT 10;

-- Rebuild indexes if needed
REINDEX INDEX idx_embeddings_vector;
```

### Poor Results

1. **Check embeddings**: Ensure all chunks are embedded
2. **Tune RRF k**: Experiment with different values
3. **Add filters**: Narrow search scope
4. **Expand context**: Include related code

### High Memory Usage

```python
# Reduce batch sizes
retriever = CodeRetriever(
    connector,
    batch_size=50  # Default: 100
)
```

---

## Next Steps

- [API Reference](../reference/retrieval.md): Complete retrieval API
- [Indexing Pipeline](indexing-pipeline.md): Understanding the indexed data
- [Production Guide](../deployment/production.md): Scaling retrieval
