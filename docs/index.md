# Welcome to Sheep Codebase Indexer

**Enterprise-Grade Code Intelligence for AI Agents**

The **Sheep Codebase Indexer** is a production-ready library that transforms source code into a queryable **Code Property Graph (CPG)** with semantic embeddings. Built for AI-powered code analysis, intelligent retrieval, and agentic workflows.

---

## Why This Exists

Standard text-based RAG fails on code because **code is structured, not prose**. A function named `process_data()` is meaningless without understanding:
- Where it's defined
- Who calls it  
- What types it uses
- Its dependencies

**Sheep Codebase Indexer** solves this by:

1. **Parsing** code structure into an AST-based graph
2. **Embedding** semantically meaningful chunks (not random lines)
3. **Linking** chunks via graph edges (`calls`, `inherits_from`, `references`)
4. **Retrieving** with hybrid search (vector + keyword + graph traversal)

---

## Key Features

### 🔍 **Precise Code Understanding**
- **Tree-sitter Parsing**: Zero-copy, incremental AST parsing for Python, TypeScript, Go, Java, Rust
- **SCIP Integration**: Industry-standard protocol for symbol resolution and cross-references  
- **Semantic Chunking**: Respects function/class boundaries with configurable overlap

### 🗄️ **Enterprise Storage**
- **PostgreSQL + pgvector**: ACID compliance, scalability, vector search
- **Snapshot Isolation**: Atomic updates with zero-downtime reindexing
- **COPY Protocol**: 10x-50x faster bulk inserts
- **Connection Pooling**: Efficient concurrent access

### 🚀 **High Performance**
- **Parallel Processing**: Multi-process indexing with `ProcessPoolExecutor`
- **Streaming Pipeline**: Memory-efficient for large repositories
- **Incremental Updates**: Only reindex changed files
- **Batch Embeddings**: Optimized vector generation

### 🔎 **Advanced Retrieval**
- **Hybrid Search**: Vector similarity + Full-text search with RRF
- **Graph Traversal**: Navigate call graphs, inheritance, dependencies
- **Context Expansion**: Auto-include related code (callers, callees, definitions)
- **Multi-Stage Pipeline**: Resolution → Search → Expansion

---

## Architecture Overview

```
Source Code → Tree-sitter Parser → Semantic Chunks
                                         ↓
                    ┌────────────────────┼────────────────────┐
                    ↓                    ↓                    ↓
            SCIP Relations        Embeddings          PostgreSQL Storage
                    └────────────────────┴────────────────────┘
                                         ↓
                              Knowledge Graph (CPG + Vectors)
```

The system follows a **multi-stage pipeline**:

1. **Parsing**: Tree-sitter extracts AST and creates semantic chunks
2. **Relation Extraction**: SCIP identifies definitions, references, calls
3. **Embedding**: Chunks are vectorized for semantic search
4. **Storage**: Graph stored in PostgreSQL with pgvector
5. **Retrieval**: Hybrid search + graph traversal for context

See [Architecture Guide](guides/architecture.md) for detailed design.

---

## Quick Start

### Installation

```bash
pip install sheep-codebase-indexer
```

See [Installation Guide](getting-started/installation.md) for detailed setup.

### Index a Repository

```python
from code_graph_indexer import CodebaseIndexer
from code_graph_indexer.storage.connector import PooledConnector

# Initialize
connector = PooledConnector(
    db_url="postgresql://user:pass@localhost:5432/codebase"
)

indexer = CodebaseIndexer(
    repo_path="./my-project",
    storage_connector=connector
)

# Index
indexer.index(
    repo_url="https://github.com/org/repo",
    branch="main"
)
```

### Search and Retrieve

```python
from code_graph_indexer import CodeRetriever

retriever = CodeRetriever(connector)

# Hybrid search (vector + keyword)
results = retriever.search(
    query="authentication middleware",
    limit=10,
    include_context=True
)
```

See [Quickstart Guide](getting-started/quickstart.md) for complete examples.

---

## Documentation Structure

### Getting Started
- [**Installation**](getting-started/installation.md): Setup with Docker and troubleshooting
- [**Quickstart**](getting-started/quickstart.md): Index your first repo in 5 minutes

### Guides
- [**Architecture**](guides/architecture.md): System design and components
- [**Data Model**](guides/data_model.md): Database schema and relationships
- [**Indexing Pipeline**](guides/indexing-pipeline.md): Parsing and chunking internals
- [**Embedding Strategy**](guides/embedding-strategy.md): Vector generation and optimization
- [**Retrieval Strategies**](guides/retrieval-strategies.md): Hybrid search and graph traversal

### API Reference
- [**Indexer**](reference/indexer.md): `CodebaseIndexer` API
- [**Storage**](reference/storage.md): `PostgresGraphStorage` API
- [**Retrieval**](reference/retrieval.md): `CodeRetriever`, `SearchExecutor`, `GraphWalker`
- [**Parsing**](reference/parsing.md): `TreeSitterRepoParser`, `SCIPIndexer`
- [**Embedding**](reference/embedding.md): `CodeEmbedder`, `EmbeddingProvider`

### Testing & Deployment
- [**Testing Guide**](testing/unit-tests-guide.md): Testing philosophy and practices
- [**Production Deployment**](deployment/production.md): PostgreSQL tuning and scaling

### Contributing
- [**Development Setup**](contributing/development-setup.md): Dev environment and workflow
- [**Code of Conduct**](contributing/code-of-conduct.md): Community guidelines

---

## Use Cases

### AI Code Assistants
Provide LLMs with precise, contextual code snippets.

### Code Search & Navigation  
Find definitions, usages, and relationships across large codebases.

### Impact Analysis
Understand ripple effects of code changes.

### Documentation Generation
Auto-generate docs with call graphs and usage examples.

---

## Performance

| Repository | Files | LOC | Indexing Time | Memory |
|-----------|-------|-----|---------------|--------|
| Small | 150 | 15K | 45s | 500MB |
| Medium | 1,200 | 120K | 6m | 2GB |
| Large | 8,500 | 850K | 42m | 8GB |

*MacBook Pro M1, 16GB RAM, PostgreSQL 14*

---

## Contributing

We welcome contributions! See [CONTRIBUTING.md](contributing/code-of-conduct.md) for guidelines.

---

## License

[MIT License](../LICENSE)

---

## Support

- **Documentation**: Full docs at [guides](guides/architecture.md)
- **Issues**: [GitHub Issues](https://github.com/your-org/sheep-codebase-indexer/issues)
- **Discussions**: [GitHub Discussions](https://github.com/your-org/sheep-codebase-indexer/discussions)
