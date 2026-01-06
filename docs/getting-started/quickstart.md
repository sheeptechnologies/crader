# Quickstart

This guide will help you index a repository and perform your first semantic search in under 10 minutes.

## Prerequisites

*   **Docker** (for PostgreSQL)
*   **Python 3.11+**
*   **OpenAI API Key** (or another embedding provider)

## 1. Start Infrastructure

Use the provided `docker-compose.yml` to spin up PostgreSQL with `pgvector`:

```bash
docker-compose up -d db
```

## 2. Install Library

```bash
pip install -e .
```

## 3. Creating the Index (Python Script)

Create a file named `index_repo.py`. We will use the `CodebaseIndexer` to clone, parse, and embed the `flask` repository.

```python
import os
import asyncio
from code_graph_indexer.indexer import CodebaseIndexer
from code_graph_indexer.providers.embedding import OpenAIEmbeddingProvider

# 1. Configuration
DB_URL = "postgresql://sheep_user:sheep_password@localhost:6432/sheep_index"
REPO_URL = "https://github.com/pallets/flask.git"
BRANCH = "main"

async def main():
    # 2. Init Indexer
    # The indexer manages cloning and DB connection pools automatically.
    indexer = CodebaseIndexer(REPO_URL, BRANCH, db_url=DB_URL)
    
    # 3. Init Provider (Async)
    # Use a cost-effective model like text-embedding-3-small
    provider = OpenAIEmbeddingProvider(
        model="text-embedding-3-small", 
        max_concurrency=10
    )
    
    try:
        # 4. Phase 1: Indexing (Parsing & Graph Construction)
        print("Starting Indexing (Parsing, SCIP)...")
        snapshot_id = indexer.index(force=False)
        print(f"Active Snapshot: {snapshot_id}")

        # 5. Phase 2: Embedding (Async Pipeline)
        print("Starting Embedding pipeline...")
        async for update in indexer.embed(provider, batch_size=200):
            status = update['status']
            if status == 'embedding_progress':
                print(f"   Processing... {update.get('total_embedded')} vectors", end='\r')
            elif status == 'completed':
                print(f"\nDone! New vectors: {update.get('newly_embedded')}")

    finally:
        indexer.close()

if __name__ == "__main__":
    asyncio.run(main())
```

Run it:
```bash
export OPENAI_API_KEY="sk-..."
python index_repo.py
```

## 4. Semantic Search

Now that the data is indexed, let's search it. Create `search.py`:

```python
import asyncio
from code_graph_indexer.indexer import CodebaseIndexer
from code_graph_indexer.retriever import CodeRetriever
from code_graph_indexer.providers.embedding import OpenAIEmbeddingProvider

DB_URL = "postgresql://sheep_user:sheep_password@localhost:6432/sheep_index"
REPO_URL = "https://github.com/pallets/flask.git"

async def main():
    # We reuse the indexer to get access to storage
    indexer = CodebaseIndexer(REPO_URL, "main", db_url=DB_URL)
    provider = OpenAIEmbeddingProvider(model="text-embedding-3-small")
    
    # Init Retriever
    retriever = CodeRetriever(indexer.storage, provider)
    
    # Resolve Repository ID
    repo_id = indexer.storage.get_repository(indexer.storage.ensure_repository(REPO_URL, "main", "flask"))['id']
    
    query = "How does request routing work?"
    print(f"Searching for: '{query}'...")
    
    results = retriever.retrieve(
        query, 
        repo_id=repo_id, 
        limit=3, 
        strategy="hybrid"
    )
    
    for r in results:
        print("\n" + "="*50)
        print(f"File: {r.file_path} (Line {r.start_line})")
        print(f"Score: {r.score:.4f}")
        print(f"Labels: {r.semantic_labels}")
        print("-" * 50)
        print(r.content[:200] + "...")

if __name__ == "__main__":
    asyncio.run(main())
```
