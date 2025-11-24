# Code Graph Indexer

**Agentic RAG for AI Developers**

`code_graph_indexer` is a memory-efficient Python library designed to index codebases into a Knowledge Graph. It leverages Tree-sitter for accurate parsing and SCIP (Semantic Code Intelligence Protocol) for precise code intelligence, enabling advanced Agentic RAG (Retrieval-Augmented Generation) workflows.

## Key Features

-   **Precise Indexing**: Uses Tree-sitter to parse code into a structured Knowledge Graph.
-   **Semantic Intelligence**: Integrates SCIP to understand symbol definitions, references, and relationships.
-   **Memory Efficient**: Designed to handle large codebases without overwhelming system resources.
-   **Graph-Based**: Stores code structure as nodes (files, chunks) and edges (structural relationships), perfect for graph traversal and complex queries.

## Roadmap

The project is currently in the **Indexing** phase. Future updates will include:

-   **Embedding**: Vectorization of graph nodes for semantic similarity search.
-   **Retrieving**: Advanced retrieval strategies combining semantic search with graph traversal.
-   **Reindexing**: Efficient incremental updates to keep the graph in sync with code changes.

## Installation

Ensure you have Python 3.10+ installed.

```bash
pip install code-graph-indexer
```

*Note: Since this is currently a local project, you might need to install it in editable mode or from source:*

```bash
git clone https://github.com/your-username/sheep-codebase-indexer.git
cd sheep-codebase-indexer
pip install -e .
```

## Quick Start

Here's how to index a local repository and query the generated graph:

```python
from code_graph_indexer import CodebaseIndexer

# Initialize the indexer with the path to your repository
indexer = CodebaseIndexer(repo_path="./path/to/your/repo")

# Run the indexing process
indexer.index()

# Access the indexed data
stats = indexer.get_stats()
print(f"Indexing complete: {stats}")

# Retrieve nodes and edges
nodes = indexer.get_nodes()
edges = indexer.get_edges()

print(f"Total nodes: {len(nodes)}")
print(f"Total edges: {len(edges)}")

# Clean up resources
indexer.close()
```

## Documentation

For more detailed information about the system architecture and design decisions, please refer to the [Architecture Guide](docs/ARCHITECTURE.md).

## Contributing

We welcome contributions! Please see [CONTRIBUTING.md](CONTRIBUTING.md) for details on how to set up your development environment and submit pull requests.

## License

[MIT License](LICENSE)
