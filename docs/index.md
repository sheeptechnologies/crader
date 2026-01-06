# Welcome to Sheep Codebase Indexer

The **Sheep Codebase Indexer** is an enterprise-grade library designed to transform raw source code into a queryable, semantic **Knowledge Graph (CPG - Code Property Graph)**. 

Unlike simple "RAG over text" solutions, this library understands the *structure* of code, enabling AI agents and developers to query dependencies, find usages, and navigate large monorepos with precision.

## Key Features

*   **High-Performance Parsing**: Uses **Tree-sitter** for robust, zero-copy parsing of modern languages (Python, TypeScript, Go, Java, Rust).
*   **Code Property Graph (CPG)**: Builds a rich graph connecting *Definitions*, *References*, *Calls*, and *Inheritance*.
*   **Hybrid Search**: Combines **Vector Search** (semantic understanding) with **Keyword Search** (exact matching) via **Reciprocal Rank Fusion (RRF)**.
*   **Enterprise Storage**: Built on **PostgreSQL** with `pgvector`, ensuring ACID compliance, scalability, and robust concurrency.
*   **Precision Retrieval**: Implements a multi-stage retrieval pipeline (Resolution -> Search -> Expansion) to provide contextually relevant code snippets to LLMs.
*   **Scalable Architecture**: Designed for distributed indexing with separation of concerns between API (Readers) and Workers (Writers).

## Why Use This?

Standard text embeddings fail on code because code is highly structured. A function named `process_data` means nothing without knowing *where* it is defined, *who* calls it, and *what* types it uses. 

**Sheep Codebase Indexer** solves this by:
1.  **Parsing** the code structure into a graph.
2.  **Embedding** semantically meaningful chunks (not just random lines).
3.  **Linking** chunks via graph edges (e.g., `calls`, `inherits_from`).

## Where to Start?

*   [**Installation**](getting-started/installation.md): Set up the library and its dependencies.
*   [**Quickstart**](getting-started/quickstart.md): Index your first repository in 5 minutes.
*   [**Architecture**](guides/architecture.md): Understand the Indexing Pipeline and Storage Schema.
*   [**API Reference**](reference/indexer.md): Detailed documentation for developers.
