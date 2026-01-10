# Frequently Asked Questions

## General Concepts

### What is Crader?
Think of it as **"Google Indexing for your Private Codebase"**, but built specifically for AI Agents.
It is an ingestion engine that transforms raw source code (from Git) into a structured **Knowledge Graph** stored in PostgreSQL. It captures not just the *text* of the code, but the *relationships* (who calls whom, where is this defined, class hierarchy).

### How is this different from standard RAG (LangChain/LlamaIndex)?
Standard RAG treats code as plain text documents. It splits files into chunks and embeds them.
*   **Standard RAG**: "Find lines similar to 'login error'." -> Returns random snippets containing "login".
*   **We**: "Find the definition of `login` and its top 3 callers." -> Returns the exact function body and the graph edges connecting it to the controllers that use it.
**We provide Structure.** You can easily wrap our `CodeRetriever` as a generic "Tool" within LangChain or LlamaIndex.

### What is a "Code Property Graph" (CPG)?
It's a data structure that combines:
1.  **Abstract Syntax Tree (AST)**: The grammar of the code (Functions, Classes).
2.  **Dependency Graph**: semantic links (Imports, Calls, Inheritance).
3.  **Embeddings**: Vector representation of the code's meaning.
We store all of this in a unified schema so you can query: *"Give me the embedding for the Function defined at line 50 that calls `User.save()`"*.

## Use Cases

### What can I build with this?
1.  **Context-Aware Coding Agents**: An agent that doesn't hallucinate libraries because it can see the actual method signatures in the project.
2.  **Repository Q&A**: A chatbot that answers "How does the billing system handle retries?" by traversing the call graph of the retry middleware.
3.  **Automated Refactoring**: Identify all 50 files that import a deprecated module to plan a migration.
4.  **Onboarding Assistants**: Help new engineers navigate legacy codebases by explaining *flows* rather than just files.

### Is it suitable for Production?
**Yes.** The system uses an **Eventual Consistency** model with **Snapshot Isolation**.
*   You can index a new commit in the background.
*   Your users continue searching the "live" snapshot without interruption.
*   Once indexing is done, you atomically "swap" to the new snapshot.
This is the same architectural pattern used by heavy-duty search engines.

## Architecture & Design

### Why PostgreSQL instead of a dedicated Vector DB?
We believe in **keeping the stack simple**. PostgreSQL 15+ with `pgvector` offers:
1.  **Transactional Integrity (ACID)**: We ensure the graph edges and the embeddings are always in sync.
2.  **Complex JOINs**: usage requires joining relational data (graph edges) with vector similarity. Postgres does this natively.
3.  **Operational Maturity**: Most teams already run Postgres. No need to manage a new piece of infrastructure like Pinecone or Weaviate just for this.

### Why do you use both Tree-sitter and SCIP?
They solve different problems:
*   **Tree-sitter** is our "Parser". It is fast, runs locally, and understands the *syntax* (Where does the function start/end?).
*   **SCIP** (Source Code Indexing Protocol) is our "Linker". It understands *semantics* (This usage of `User` refers to `models.py`).
By combining them, we get the speed of regex-free parsing with the precision of a compiler.

## Operations & Troubleshooting

### Does it support huge Monorepos?
**Yes.**
*   **Filtering**: You can ignore `node_modules`, `vendor`, or specific folders via `GLOBAL_IGNORE_DIRS`.
*   **Incremental Indexing**: We track commit hashes per file. If a file hasn't changed between commits, we reuse its existing nodes and embeddings, saving 90% of embedding costs.
*   **Parallelism**: Parsing and Graph construction happen in parallel worker processes.

### Can I use local LLMs (Ollama / Llama.cpp)?
**Yes.**
The `EmbeddingProvider` is an abstract base class. You can implement a subclass that calls your local inference server (e.g., using `langchain` or direct HTTP calls) and pass it to the indexer.
We default to OpenAI (`text-embedding-3-small`) because it provides the best cost/performance ratio for code today.

### `Snapshot locked` error
If the indexer process is kill -9'd, it might leave a snapshot in `indexing` state.
*   **Solution**: Run `indexer.index(force=True, force_new=True)` to ignore the lock and start a fresh snapshot. The orphan snapshot will be cleaned up by the auto-pruner eventually.

### Search returns irrelevant results
*   **Check Filters**: Are you filtering by `language='python'` but searching a TypeScript repo?
*   **Check Embeddings**: Did the embedding process finish? Run `indexer.get_stats()` to see if `embeddings` count matches `total_nodes`.
*   **Tweak Strategy**: If looking for specific error codes (e.g., `ERR_505`), force `strategy="keyword"`.
