# Retrieval API Reference

The `retriever` module acts as the "Brain" interface. It translates user intent (queries) into actionable code context.

::: src.code_graph_indexer.retriever

## CodeRetriever

```python
class CodeRetriever(storage: GraphStorage, embedding_provider: EmbeddingProvider)
```

The main facade class. It requires access to both the database (for keyword search/graph walk) and the embedding API (for converting queries to vectors).

### Methods

#### `retrieve`

```python
def retrieve(self, query: str, repo_id: str, snapshot_id: Optional[str] = None, limit: int = 10, strategy: str = "hybrid", filters: Dict[str, Any] = None) -> List[RetrievedContext]
```

**Description:**
Performs the end-to-end search pipeline: `Vectorize Query -> Search DB -> Fusion -> Graph Expansion`.

**Arguments:**

*   **`query`** *(str)*:
    The search string. Can be natural language ("How to auth?") or code snippets ("def login()").
    
*   **`repo_id`** *(str)*:
    The UUID of the repository to search.
    
*   **`snapshot_id`** *(Optional[str])*:
    If `None` (default), automatically resolves to the **Latest Completed Snapshot** for the repo. Providing an ID allows "Time-Travel" search on older versions.
    
*   **`limit`** *(int)*:
    The maximum number of results to return. Default 10. Note: Internally, it fetches `limit * 2` capabilities to allow for RRF re-ranking.
    
*   **`strategy`** *(str)*:
    *   `"hybrid"`: (Recommended) Runs both Vector and FTS, merges with RRF.
    *   `"vector"`: Semantic similarity only. Good for broad concepts.
    *   `"keyword"`: Text match only. Good for exact identifiers.
    
*   **`filters`** *(Dict)*:
    Metadata filters pushed down to SQL.
    *   `"path_prefix"`: `str` (e.g. `"src/api"`). Matches files starting with this string.
    *   `"language"`: `List[str]` (e.g. `["python", "js"]`). Matches file extensions.
    *   `"exclude_category"`: `List[str]` (e.g. `["test"]`). Hides test files.

**Returns:**
*   `List[RetrievedContext]`: A list of rich context objects, sorted by relevance score.

---

## Data Objects

### `RetrievedContext`

```python
@dataclass
class RetrievedContext
```

The output unit of the retrieval process.

#### Attributes

*   **`file_path`** *(str)*: Source file path relative to repo root.
*   **`content`** *(str)*: The actual code block.
*   **`start_line`**, **`end_line`** *(int)*: Line numbers (1-indexed).
*   **`score`** *(float)*: The computed relevance score (normalized 0-1).
*   **`semantic_labels`** *(List[str])*: Tags extracted during parsing (e.g. `["class_definition", "public_api"]`).

#### Enriched Attributes (Graph Context)
*Attributes populated by the `GraphWalker` step.*

*   **`parent_context`** *(Dict)*: Information about the containing scope.
    *   Example: `{"type": "class", "name": "PaymentProcessor", "line": 45}`.
    *   *Usage*: Tells the LLM *where* this function lives.

*   **`outgoing_definitions`** *(List[Dict])*: Summaries of external symbols used by this code.
    *   Example: `[{"name": "stripe.charge", "role": "call"}, {"name": "User", "role": "instantiation"}]`.
    *   *Usage*: Tells the LLM *what dependencies* this function has, without needing to retrieve those files.

#### Methods

*   **`render()`** -> `str`
    Returns a Markdown-formatted representation optimized for LLM prompting.
    ```markdown
    ### File: src/main.py (L10-20)
    [CONTEXT] Inside class App
    [CODE]
    def run(): ...
    [RELATIONS] Calls: logger.info, db.connect
    ```
