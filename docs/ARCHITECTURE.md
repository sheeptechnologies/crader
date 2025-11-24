# Architecture Guide

This document outlines the architecture of `code_graph_indexer`, a system designed to enable Agentic RAG by indexing codebases into a semantic Knowledge Graph.

## High-Level Overview

The system operates in four main stages, transforming raw source code into a queryable knowledge base:

1.  **Indexing**: Parsing code and building the initial graph structure.
2.  **Embedding** (Planned): Generating vector embeddings for semantic search.
3.  **Retrieving** (Planned): Querying the graph using hybrid search (semantic + structural).
4.  **Reindexing** (Planned): Handling incremental updates efficiently.

## 1. Indexing

The indexing phase is the foundation of the system. It converts file contents into a structured graph.

### Workflow

1.  **Input**: A local repository path.
2.  **Parsing (Tree-sitter)**:
    -   The `TreeSitterRepoParser` iterates through files.
    -   It breaks down code into **Chunks** (functions, classes, methods) based on AST structure.
    -   It identifies **Structural Relationships** (e.g., `child_of`, `defines`).
3.  **SCIP Integration**:
    -   `SCIPIndexer` and `SCIPRunner` analyze the code to find precise symbol definitions and references.
    -   This data enriches the graph with "semantic" edges (e.g., `references`, `calls`).
4.  **Graph Construction**:
    -   `KnowledgeGraphBuilder` aggregates data from the parser and SCIP.
    -   It creates nodes for Files and Chunks.
    -   It creates edges for structural and semantic relationships.
5.  **Storage**:
    -   Data is persisted using `SqliteGraphStorage` (currently) into a relational database optimized for graph-like queries.

### Key Components

-   **`CodebaseIndexer`**: The main orchestrator.
-   **`TreeSitterRepoParser`**: Handles syntax-aware chunking.
-   **`SCIPIndexer`**: Extracts cross-file references and definitions.
-   **`SqliteGraphStorage`**: Manages persistence.

## 2. Embedding (Future)

The next phase involves adding a semantic layer to the graph.

-   **Goal**: Enable natural language queries like "Find the function that handles user authentication."
-   **Strategy**:
    -   Generate vector embeddings for each **ChunkNode** (function/class bodies).
    -   Store embeddings in a vector store (or a vector-enabled column in the DB).
    -   Use models optimized for code (e.g., CodeBERT, OpenAI text-embedding-3).

## 3. Retrieving (Future)

Retrieval will combine the strengths of graph traversal and vector search.

-   **Hybrid Search**:
    1.  **Vector Search**: Find entry points relevant to the user's query.
    2.  **Graph Traversal**: "Walk" the graph from those entry points to find related context (e.g., "Who calls this function?", "What does this class inherit from?").
-   **Agentic Workflow**: The retriever will provide tools for an AI agent to autonomously navigate the codebase.

## 4. Reindexing (Future)

To support active development, the system must handle changes without rebuilding the entire index.

-   **Incremental Updates**:
    -   Detect changed files (via Git or file mtime).
    -   Re-parse only affected files.
    -   Update the graph: remove old nodes/edges, add new ones.
    -   Update embeddings for changed chunks.
