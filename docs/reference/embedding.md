# Embedding API

The embedding module generates vector embeddings for chunks. It runs asynchronously and uses a staging table to deduplicate work.

## CodeEmbedder

```python
from crader.embedding.embedder import CodeEmbedder

embedder = CodeEmbedder(storage, provider)
```

### run_indexing

```python
async for update in embedder.run_indexing(snapshot_id, batch_size=1000, mock_api=False):
    ...
```

Status updates include stages such as `init`, `staging_progress`, `deduplicating`, `embedding_progress`, and `completed`.

### Prompt construction

`_compute_prompt_and_hash` builds the prompt and hash used for deduplication. The prompt includes:

- File path, language, and category
- Semantic roles and tags (from parser metadata)
- Incoming definitions (symbols that resolve to this node)
- Code content

The SHA-256 hash of this prompt is stored as `vector_hash`.

## EmbeddingProvider

Providers must implement:

- `embed(texts: List[str]) -> List[List[float]]`
- `embed_async(texts: List[str]) -> List[List[float]]`
- `dimension` and `model_name` properties

Built-in providers:

- `OpenAIEmbeddingProvider`
- `FastEmbedProvider`
- `DummyEmbeddingProvider`
