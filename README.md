# Crader

[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](https://opensource.org/licenses/MIT)
[![Code Coverage](https://img.shields.io/badge/coverage-75%25-yellow.svg)](docs/testing/unit-tests-guide.md)

**Enterprise-grade Code Intelligence for AI Agents**

Transform your codebase into a queryable Knowledge Graph with semantic understanding. Built for AI-powered code analysis, intelligent retrieval, and agentic workflows.

---

## What is This?

**Crader** is a production-ready library that indexes source code into a **Code Property Graph (CPG)** with semantic embeddings. Unlike simple text-based RAG solutions, it understands code structure, relationships, and semantics.

### The Problem

Standard text embeddings fail on code because:
- Code is highly structured (AST, not prose)
- Context matters: `process_data()` means nothing without knowing where it's defined and who calls it
- Symbol resolution requires understanding imports, inheritance, and scoping
- Chunking arbitrary lines breaks semantic meaning

### The Solution

**Crader** provides:

1. **Structural Parsing**: Tree-sitter AST parsing for accurate code understanding
2. **Semantic Chunking**: Intelligent code splitting that preserves meaning
3. **Graph Relationships**: Links definitions, references, calls, and inheritance
4. **Hybrid Search**: Combines vector similarity with keyword matching (RRF)
5. **Context-Aware Retrieval**: Graph traversal for relevant context expansion

---

## Key Features

### Precise Code Intelligence
- **Tree-sitter Parsing**: Zero-copy, incremental parsing for Python, TypeScript, Go, Java, Rust
- **SCIP Integration**: Industry-standard protocol for symbol resolution and cross-references
- **Semantic Chunking**: Respects function/class boundaries with configurable overlap

### Supported Languages

| Language | Support Status | Note |
|----------|----------------|------|
| **Python** | ✅ Supported | Full indexing, retrieval, and graph capabilities |
| JavaScript | 🚧 Coming Soon | Planned |
| TypeScript | 🚧 Coming Soon | Planned |
| Go | 🚧 Coming Soon | Planned |
| Java | 🚧 Coming Soon | Planned |
| Rust | 🚧 Coming Soon | Planned |

### Enterprise Storage
- **PostgreSQL + pgvector**: ACID compliance, scalability, and vector search
- **Snapshot Isolation**: Atomic updates with zero-downtime reindexing
- **COPY Protocol**: High-performance bulk inserts (10x-50x faster than INSERT)
- **Connection Pooling**: Efficient resource management for concurrent access

### High Performance
- **Parallel Processing**: Multi-process indexing with `ProcessPoolExecutor`
- **Streaming Pipeline**: Memory-efficient processing of large repositories
- **Incremental Updates**: Only reindex changed files
- **Batch Embeddings**: Optimized vector generation with configurable batch sizes

### Advanced Retrieval
- **Hybrid Search**: Vector similarity + Full-text search with Reciprocal Rank Fusion
- **Graph Traversal**: Navigate call graphs, inheritance hierarchies, and dependencies
- **Context Expansion**: Automatically include related code (callers, callees, definitions)
- **Multi-Stage Pipeline**: Resolution → Search → Expansion for optimal results

---

## 📊 Architecture Overview

```
┌─────────────────┐
│  Source Code    │
└────────┬────────┘
         │
         ▼
┌─────────────────┐      ┌──────────────────┐
│  Tree-sitter    │─────▶│  Code Chunks     │
│  Parser         │      │  (Semantic)      │
└─────────────────┘      └────────┬─────────┘
                                  │
         ┌────────────────────────┼────────────────────────┐
         │                        │                        │
         ▼                        ▼                        ▼
┌─────────────────┐      ┌──────────────────┐    ┌──────────────────┐
│  SCIP Indexer   │      │  Embedding       │    │  PostgreSQL      │
│  (Relations)    │      │  Generation      │    │  Storage         │
└────────┬────────┘      └────────┬─────────┘    └────────┬─────────┘
         │                        │                        │
         └────────────────────────┴────────────────────────┘
                                  │
                                  ▼
                         ┌──────────────────┐
                         │  Knowledge Graph │
                         │  (CPG + Vectors) │
                         └──────────────────┘
```

See [Architecture Guide](docs/guides/architecture.md) for detailed design.

---


## 🚀 Quick Start

Get your codebase indexed and queryable in under 5 minutes.

### 1. Prerequisites

You need a PostgreSQL database with the `pgvector` extension enabled.

**Using Docker (Recommended):**

```bash
docker run -d \
  --name sheep-postgres \
  -e POSTGRES_USER=user \
  -e POSTGRES_PASSWORD=pass \
  -e POSTGRES_DB=codebase \
  -p 5432:5432 \
  pgvector/pgvector:pg14
```

### 2. Installation

```bash
pip install crader
```

### 3. Complete Example

Here is a complete script to index a repository and perform a semantic search.

```python
import os
from crader import CodebaseIndexer, CodeRetriever
from crader.storage.connector import PooledConnector

# 1. Setup Database Connection
db_url = os.getenv("DB_URL", "postgresql://user:pass@localhost:5432/codebase")
connector = PooledConnector(db_url=db_url)

# 2. Initialize the Indexer
indexer = CodebaseIndexer(
    repo_path="./my-target-repo",  # Path to local repo clone
    storage_connector=connector,
    languages=["python"],          # Only Python is currently supported
)

# 3. Index the Repository
# This parses the code, builds the graph, and generates embeddings (requires OPENAI_API_KEY)
# You can skip this step if the repo is already indexed
print("Indexing repository...")
indexer.index(
    repo_url="https://github.com/my-org/my-target-repo",
    branch="main",
    force_reindex=False
)

# 4. Perform a Hybrid Search
# Initialize the retriever with the same connector
retriever = CodeRetriever(connector)

query = "How is the authentication middleware implemented?"

print(f"\nSearching for: '{query}'...")
results = retriever.search(
    query=query,
    limit=3,
    include_context=True,   # Fetch surrounding code context
    repo_id="my-target-repo" # Filter by repo
)

# 5. Display Results
for i, result in enumerate(results, 1):
    print(f"\n--- Result {i} (Score: {result['score']:.4f}) ---")
    print(f"File: {result['file_path']}:{result['start_line']}")
    print(f"Type: {result['metadata'].get('node_type', 'chunk')}")
    print(f"Content Preview:\n{result['content'][:200]}...")
```

### 4. Graph Navigation

Beyond search, you can traverse the knowledge graph to understand dependencies.

```python
from crader import CodeNavigator

navigator = CodeNavigator(connector)

# Example: Find what calls 'process_data'
callers = navigator.get_incoming_calls(function_name="process_data")
print(f"Found {len(callers)} callers for 'process_data'")
```

---

## 📚 Documentation

| Resource | Description |
|----------|-------------|
| [**Installation Guide**](docs/getting-started/installation.md) | Detailed setup instructions with Docker |
| [**Quickstart**](docs/getting-started/quickstart.md) | Index your first repo in 5 minutes |
| [**Architecture**](docs/guides/architecture.md) | System design and components |
| [**API Reference**](docs/reference/indexer.md) | Complete API documentation |
| [**Testing Guide**](docs/testing/unit-tests-guide.md) | Testing philosophy and practices |

---

## Use Cases

### 1. AI Code Assistants
Provide LLMs with precise, contextual code snippets instead of random text chunks.

```python
# Get relevant context for a coding question
context = retriever.get_context_for_query(
    "How does authentication work?",
    max_tokens=4000
)
```

### 2. Code Search & Navigation
Find definitions, usages, and relationships across large codebases.

```python
# Find all implementations of an interface
implementations = navigator.find_implementations("IHandler")
```

### 3. Impact Analysis
Understand the ripple effects of code changes.

```python
# Find all code affected by changing a function
impact = navigator.analyze_impact(node_id="func_456")
```

### 4. Documentation Generation
Auto-generate documentation with call graphs and usage examples.

```python
# Get comprehensive function documentation
docs = navigator.get_function_docs(
    function_name="process_request",
    include_callers=True,
    include_examples=True
)
```

---

## Configuration

### Environment Variables

```bash
# Database
export DB_URL="postgresql://user:pass@localhost:5432/codebase"

# Embedding Provider
export EMBEDDING_PROVIDER="openai"  # or "cohere", "local"
export OPENAI_API_KEY="sk-..."

# Performance Tuning
export MAX_WORKERS=8
export BATCH_SIZE=100
export CHUNK_SIZE=512
export CHUNK_OVERLAP=50
```

### Advanced Configuration

```python
indexer = CodebaseIndexer(
    repo_path="./project",
    storage_connector=connector,
    max_workers=8,           # Parallel indexing processes
    chunk_size=512,          # Tokens per chunk
    chunk_overlap=50,        # Overlap for context
    languages=["python", "typescript"],  # Filter languages
    exclude_patterns=["**/test_*.py", "**/__pycache__"]
)
```

---



## Configuration

You can configure `crader` by passing arguments via CLI or setting environment variables in a `.env` file (loaded from the current working directory).

| Variable | CLI Flag | Default | Description |
| :--- | :--- | :--- | :--- |
| `CRADER_DB_URL` | `--db-url` | `None` | Connection string for the PostgreSQL database (e.g. `postgresql://user:pass@localhost:5432/dbname`) |
| `CRADER_REPO_VOLUME` | N/A | `./sheep_data/repositories` | Local path where git repositories are cloned |

### CLI Usage

```bash
# Using CLI arguments
crader index https://github.com/user/repo --db-url postgresql://...

# Using Environment Variables (.env)
# Create a .env file:
# CRADER_DB_URL=postgresql://user:pass@localhost:5432/dbname
crader index https://github.com/user/repo
```

## Contributing

We welcome contributions! See [CONTRIBUTING.md](CONTRIBUTING.md) for:
- Development setup
- Code style guide
- Testing requirements
- Pull request process

### Development Setup

```bash
# Clone and install
git clone https://github.com/your-org/crader.git
cd crader
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

# Run tests
make test-unit
make test-integration
make coverage

# Build docs
mkdocs serve
```

---

## Troubleshooting

### Common Issues

**PostgreSQL connection fails**
```bash
# Check PostgreSQL is running
pg_isready -h localhost -p 5432

# Verify pgvector extension
psql -c "CREATE EXTENSION IF NOT EXISTS vector;"
```

**Out of memory during indexing**
```python
# Reduce batch size and workers
indexer = CodebaseIndexer(
    max_workers=2,
    batch_size=50
)
```

**Slow queries**
```sql
-- Add indexes for common queries
CREATE INDEX idx_nodes_file_path ON nodes(file_path);
CREATE INDEX idx_edges_source_target ON edges(source_id, target_id);
```

See [FAQ](docs/faqs.md) for more solutions.

---

## License

[MIT License](LICENSE) - see LICENSE file for details.

---

## Acknowledgments

Built with:
- [Tree-sitter](https://tree-sitter.github.io/) - Incremental parsing
- [SCIP](https://github.com/sourcegraph/scip) - Code intelligence protocol
- [PostgreSQL](https://www.postgresql.org/) + [pgvector](https://github.com/pgvector/pgvector) - Vector storage
- [psycopg3](https://www.psycopg.org/psycopg3/) - PostgreSQL driver

---

## Support

- **Documentation**: [https://your-docs-site.com](https://your-docs-site.com)
- **Issues**: [GitHub Issues](https://github.com/your-org/crader/issues)
- **Discussions**: [GitHub Discussions](https://github.com/your-org/crader/discussions)
- **Email**: support@your-org.com
