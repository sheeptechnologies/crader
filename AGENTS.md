# AGENTS.md - The AI Agent's Guide to Crader

Welcome, Agent. This document is your primary source of truth for understanding, navigating, and modifying the **Crader** codebase.

## 🧠 Project Identity
**Crader** (Code Reader/Grader) is a high-performance code analysis engine (Python) that transforms raw source code into a **Code Property Graph (CPG)**.
It bridges the gap between static analysis and semantic search, enabling:
1.  **Precise Code Navigation** (Go to definition, Find references).
2.  **Semantic Search** (Vector embeddings via OpenAI).
3.  **Graph RAG** (Retrieval Augmented Generation with structural context).

## 🏗 System Architecture

### 1. The Core Pipeline (`src/crader`)
-   **`indexer.py` (CodebaseIndexer)**: The main entry point. It orchestrates the flow:
    1.  `GitVolumeManager`: Clones/updates the repo.
    2.  `LanguageParser`: Parses files using Tree-Sitter.
    3.  `GraphBuilder`: Constructs the graph nodes/edges in memory.
    4.  `Embedder`: Generates vector embeddings for code chunks.
    5.  `Storage`: Persists everything to the DB.

### 2. Storage Layer (`src/crader/storage`)
-   **`postgres.py` (PostgresGraphStorage)**: The production backend.
    -   **Table `nodes`**: Stores code entities (Functions, Classes, Files).
    -   **Table `edges`**: Stores relationships (`calls`, `inherits`, `imports`).
    -   **Table `node_embeddings`**: Stores `pgvector` embeddings.
-   **Schema**: Relational + Graph + Vector hybrid.

### 3. Retrieval & Intelligence (`src/crader/retriever.py`, `src/crader/navigator.py`)
-   **CodeRetriever**: Implements "Hybrid Search" (Vector Similarity + BM25/Keyword + Graph Filters).
-   **CodeNavigator**: Tool for structural traversals (e.g., `get_call_hierarchy`, `get_class_hierarchy`).

### 4. Debugger Tools (`tools/debugger`)
-   A standalone FastAPI application (`server.py`) that provides a visual interface to inspect the generated graph, run queries, and debug the agent tools.

## 🛠 Development Rules

### 1. Code Style
-   **Linter**: `ruff`. Always run `ruff check . --fix` before committing.
-   **Formatting**: Handled by `ruff`.

### 2. Testing
-   **Unit Tests**: `pytest tests/unit` (Fast, mocked).
-   **Integration Tests**: `pytest tests/integration` (Requires working Postgres DB).
-   **Mocking**: We heavily use `unittest.mock` to avoid API calls during tests.

### 3. Environment Variables
Agents should be aware of these keys:
-   `CRADER_DB_URL`: Postgres connection string.
-   `CRADER_OPENAI_API_KEY`: For generating embeddings.

## 🧩 Key Data Structures
When manipulating the graph, think in terms of:
-   **Node**: `(id, type, file_path, start_line, end_line, content, metadata)`
-   **Edge**: `(source_id, target_id, relation_type)`

## 🤖 Common Agent Tasks
If you are asked to...
-   **"Fix a parser error"**: Look into `src/crader/parsing/`. Check `tree-sitter` queries in `*.scm` files (if any) or the python logic.
-   **"Add a new specific language"**: You'll need to update `src/crader/parsing/languages.py` and ensure `tree-sitter-LANGUAGE` is installed.
-   **"Improve retrieval"**: Check `CodeRetriever` in `src/crader/retriever.py`.

## 🧠 Knowledge Preservation
If you discover something important about the codebase (undocumented behaviors, tricky edge cases, architectural insights), **do not keep it to yourself**.
-   **Update this file**: Add your findings to `AGENTS.md`.
-   **Module-specific Knowledge**: If the knowledge is specific to a submodule, create a new `AGENTS.md` inside that module's directory (e.g., `src/crader/parsing/AGENTS.md`) and document it there.
