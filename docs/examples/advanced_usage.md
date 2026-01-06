# Advanced Usage & Recipes

This guide covers scenarios beyond the basic "Quickstart", tailored for enterprise deployments and power users.

## 1. Indexing Strategy

### Monorepo Filtering
For large repositories, you generally want to exclude build artifacts, docs, and test fixtures to keep the graph clean and performance high.

The indexer respects `GLOBAL_IGNORE_DIRS` by default, but you can customize this by modifying `src/code_graph_indexer/parsing/parsing_filters.py` or by passing custom logic if you extend the class.

**Default Exclusions:**
*   **Technical Noise**: `node_modules`, `.git`, `dist`, `__pycache__`
*   **Semantic Noise**: `fixtures`, `migrations`, `locales`

### Branch-Specific Indexing
You can maintain separate indices for different branches (e.g., `main` vs `develop`).

```python
from code_graph_indexer import CodebaseIndexer

# index main (Production)
indexer_main = CodebaseIndexer(
    repo_url="git@github.com:org/repo.git",
    branch="main",
    db_url="postgresql://user:pass@localhost/dbname"
)
indexer_main.index()

# index feature-branch (Development)
indexer_dev = CodebaseIndexer(
    repo_url="git@github.com:org/repo.git",
    branch="feature/new-search-api",
    db_url="postgresql://user:pass@localhost/dbname"
)
indexer_dev.index()
```
*Note: The `Repository` entity in the DB is unique per (url, branch) pair.*

## 2. Advanced Search & Retrieval

### Using Metadata Filters
The `filters` argument in `retrieve()` allows you to slice the graph by any metadata field extracted during parsing. This is pushed down to the database (SQL `WHERE` clause) for maximum speed.

**Filter by Language:**
```python
results = retriever.retrieve(
    query="authentication middleware",
    repo_id=repo_id,
    filters={"language": "python"} 
)
```

**Filter by Semantic Role:**
Find only **Class Definitions** related to "User":
```python
results = retriever.retrieve(
    query="User",
    repo_id=repo_id,
    filters={"role": "class"} # derived either from 'type' or 'category' in metadata
)
```

**Filter by File Path:**
Scope search to a specific module:
```python
results = retriever.retrieve(
    query="calculate_tax",
    repo_id=repo_id,
    filters={"start_path": "src/billing/"} # Implementation depends on storage backend
)
```

### Hybrid Search Tuning
By default, `CodeRetriever` uses **Reciprocal Rank Fusion (RRF)** to combine Vector and Keyword results. You can force a specific strategy if you know what you are doing.

*   **`strategy='vector'`**: Best for concept search ("how do I login?").
*   **`strategy='keyword'`**: Best for exact error codes ("Error 503") or specific symbol names ("UserFactory").
*   **`strategy='hybrid'`**: (Default) Best of both worlds.

```python
# Force exact keyword match for an error code
hits = retriever.retrieve(
    query="ERR_CONNECTION_RESET",
    repo_id=repo_id,
    strategy="keyword"
)
```

## 3. Custom Embedding Models

The system uses the `EmbeddingProvider` interface. You can swap OpenAI with any other provider (Vertex AI, HuggingFace, Bedrock) by implementing a simple adapter.

```python
from code_graph_indexer.providers.embedding import EmbeddingProvider

class VertexEmbeddingProvider(EmbeddingProvider):
    def __init__(self, project_id, model="text-embedding-gecko"):
        self.client = ... # Initialize Vertex AI client
        
    async def embed_async(self, texts: List[str]) -> List[List[float]]:
        # Implement call to Google Cloud
        return await self.client.get_embeddings(texts)

# Usage
indexer = CodebaseIndexer(..., embedding_provider=VertexEmbeddingProvider("my-project"))
```
