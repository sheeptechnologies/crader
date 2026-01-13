# Quickstart

Index a repository and run a search in a few minutes.

## 1. Start PostgreSQL (if needed)

```bash
docker run -d --name crader-postgres \
  -e POSTGRES_USER=crader \
  -e POSTGRES_PASSWORD=crader \
  -e POSTGRES_DB=codebase \
  -p 5432:5432 \
  pgvector/pgvector:pg14
```

## 2. Configure the database

```bash
export CRADER_DB_URL="postgresql://crader:crader@localhost:5432/codebase"
crader db upgrade
```

## 3. Index a repository

```bash
crader index https://github.com/pallets/flask.git --branch main
```

This step parses files, builds chunks, and ingests cross-file relations via SCIP tooling. SCIP is currently the bottleneck for file-incremental indexing; the roadmap includes a Mycelium-based replacement (https://github.com/sheeptechnologies/mycelium.git). It does not generate embeddings.

## 4. Generate embeddings

```python
import asyncio
from crader import CodebaseIndexer
from crader.providers.embedding import OpenAIEmbeddingProvider

repo_url = "https://github.com/pallets/flask.git"
branch = "main"
db_url = "postgresql://crader:crader@localhost:5432/codebase"

indexer = CodebaseIndexer(repo_url=repo_url, branch=branch, db_url=db_url)

async def main():
    provider = OpenAIEmbeddingProvider(model="text-embedding-3-small")
    async for update in indexer.embed(provider, batch_size=200):
        if update.get("status") == "completed":
            print(update)

asyncio.run(main())
indexer.close()
```

## 5. Search

```python
from crader import CodeRetriever
from crader.providers.embedding import OpenAIEmbeddingProvider
from crader.storage.connector import PooledConnector
from crader.storage.postgres import PostgresGraphStorage

db_url = "postgresql://crader:crader@localhost:5432/codebase"
repo_url = "https://github.com/pallets/flask.git"
branch = "main"

connector = PooledConnector(dsn=db_url)
storage = PostgresGraphStorage(connector)
provider = OpenAIEmbeddingProvider(model="text-embedding-3-small")
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
)

for hit in results:
    print(hit.file_path, hit.start_line, hit.score)
```

Keyword search works without embeddings, but `CodeRetriever` still requires an embedding provider instance.
