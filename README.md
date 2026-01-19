# Crader

Crader builds a code property graph (CPG) and embeddings from Git repositories. It parses code into semantic chunks, stores them in PostgreSQL with snapshot isolation, and supports hybrid search and graph navigation.

## Core capabilities

- Tree-sitter parsing and chunking with byte-precise ranges
- SCIP-based relations for cross-file references (current bottleneck for file-incremental indexing; see [Roadmap](docs/roadmap.md))
- Full-text index for keyword search
- Vector embeddings and pgvector search
- Snapshot-based reads for consistent retrieval
- Graph navigation helpers (parent blocks, callers, callees)

## Requirements

- Python 3.10+
- PostgreSQL with the pgvector extension
- git
- SCIP CLI and language indexers (scip, scip-python, scip-typescript, scip-java, scip-go, scip-rust, scip-php, scip-clang). This is the current bottleneck for file-incremental indexing; see [Roadmap](docs/roadmap.md).
- Optional: OpenAI API key if you use OpenAI embeddings

## Install

```bash
pip install crader
```

## Database setup

Set the database URL and run the migrations:

```bash
export CRADER_DB_URL="postgresql://user:pass@localhost:5432/codebase"
crader db upgrade
```

The migration enables the `vector` extension and creates the schema used by the indexer.

## Quick start

Index a repository (parsing, chunking, SCIP relations; see [Roadmap](docs/roadmap.md) for the current bottleneck):

```bash
crader index https://github.com/pallets/flask.git --branch main
```

Generate embeddings:

```python
import asyncio
from crader import CodebaseIndexer
from crader.providers.embedding import OpenAIEmbeddingProvider

repo_url = "https://github.com/pallets/flask.git"
branch = "main"

db_url = "postgresql://user:pass@localhost:5432/codebase"
indexer = CodebaseIndexer(repo_url=repo_url, branch=branch, db_url=db_url)

async def main():
    provider = OpenAIEmbeddingProvider(model="text-embedding-3-small")
    async for update in indexer.embed(provider, batch_size=200):
        if update.get("status") == "completed":
            print(update)

asyncio.run(main())
indexer.close()
```

Search and retrieve context:

```python
from crader import CodeRetriever
from crader.storage.connector import PooledConnector
from crader.storage.postgres import PostgresGraphStorage
from crader.providers.embedding import OpenAIEmbeddingProvider

db_url = "postgresql://user:pass@localhost:5432/codebase"
provider = OpenAIEmbeddingProvider(model="text-embedding-3-small")
connector = PooledConnector(dsn=db_url)
storage = PostgresGraphStorage(connector)
retriever = CodeRetriever(storage, provider)

repo_id = storage.ensure_repository(
    repo_url,
    branch,
    repo_url.rstrip("/").split("/")[-1].replace(".git", ""),
)

results = retriever.retrieve(
    query="How does request routing work?",
    repo_id=repo_id,
    limit=3,
    strategy="hybrid",
    filters={"language": "python"},
)

for hit in results:
    print(hit.file_path, hit.start_line, hit.score)
```

Keyword search works without embeddings, but `CodeRetriever` still requires an embedding provider instance.

## Supported languages

Crader scans files by extension during indexing:

- .py
- .js, .jsx
- .ts, .tsx
- .java
- .go
- .rs
- .c, .cpp
- .php
- .html, .css

Semantic tagging via Tree-sitter queries is currently provided for Python, JavaScript, and TypeScript. SCIP relations require the relevant SCIP tools to be installed and available on PATH.

SCIP is required today to build cross-file relations.

## Configuration

Environment variables used by the runtime:

- `CRADER_DB_URL`: PostgreSQL connection string (required by CLI and indexer).
- `CRADER_REPO_VOLUME`: Root directory for cached repos and worktrees (defaults to `./sheep_data/repositories`).
- `CRADER_OPENAI_API_KEY` or `OPENAI_API_KEY`: OpenAI credentials for embeddings.

## License

MIT. See `LICENSE`.
