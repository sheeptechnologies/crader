# Embedding API

The embedding module manages the conversion of code chunks into high-dimensional vector representations. It is designed for massive throughput and cost efficiency.

::: src.code_graph_indexer.embedding.embedder

## CodeEmbedder

```python
class CodeEmbedder
```

The asynchronous engine responsible for vectorization.

### `run_indexing`

```python
async def run_indexing(self, snapshot_id: str, ...) -> AsyncGenerator
```

Executes the embedding pipeline.

### The Staging Pipeline

The embedder implements a customized **ETL (Extract, Transform, Load)** process to minimize calls to the expensive Embedding API (OpenAI/Vertex).

#### 1. Transform & Hash (CPU Parallel)
Before touching the database, the system uses a `ProcessPoolExecutor` to prepare data in parallel.
For each chunk, it computes a **Semantic Fingerprint** (`v_hash`).
*   **Formula**: `SHA256(Filepath + Struct + Content + OutgoingDefs + IncomingDefs)`
*   This hash represents the *exact* semantic state of the code.

#### 2. Staging Load (Bulk I/O)
The prepared data is loaded using `COPY` into a temporary table:
```sql
CREATE UNLOGGED TABLE temp_embedding_staging ...
```
*Unlogged tables are faster as they bypass the WAL (Write Ahead Log).*

#### 3. Deduplication (SQL Set Logic)
This is the core cost-saving mechanism.
```sql
-- "Backfill": Find existing vectors for identical code
UPDATE temp_embedding_staging t
SET embedding = e.embedding, 
    is_cached = true
FROM node_embeddings e
WHERE e.vector_hash = t.vector_hash;
```
If you move a file, or rename a folder, the `vector_hash` might change (due to path), but if the content is stable, we could potentially relax the hash strictness. Currently, it is strict.

#### 4. Delta Processing (Async Workers)
Only rows where `embedding IS NULL` are fetched.
*   **Producer**: Pushes batches to an `asyncio.Queue`.
*   **Consumers**: A pool of workers (size = `max_concurrency`) pull batches, call the API, and write results.

### `_compute_prompt_and_hash`

```python
def _compute_prompt_and_hash(node: Dict) -> Tuple[str, str]
```

Constructs the "Context Window" for the LLM. It's not just the code!

**Prompt Structure:**
1.  **Header**: File Path, Language, Category.
2.  **Semantic Tags**: Roles (e.g., `Role: API Endpoint`), Modifiers (e.g., `Tags: async, static`).
3.  **Graph Context**: Incoming Definitions (e.g., `Defines: UserFactory, AuthMiddleware`).
4.  **Body**: The actual source code.

This rich context ensures that even small chunks (like a 5-line function) have enough semantic meaning for high-quality retrieval.
