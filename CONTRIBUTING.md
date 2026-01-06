# Contributing to Code Graph Indexer

Thank you for your interest in contributing to `code_graph_indexer`! We are building a tool to empower AI developers with better code understanding capabilities.

## Development Setup

### Prerequisites

-   Python 3.10 or higher
-   `git`

### Setting up the Environment

1.  **Clone the repository:**

    ```bash
    git clone https://github.com/your-username/sheep-codebase-indexer.git
    cd sheep-codebase-indexer
    ```

2.  **Create a virtual environment:**

    ```bash
    python -m venv .venv
    source .venv/bin/activate  # On Windows use: .venv\Scripts\activate
    ```

3.  **Install dependencies:**

    ```bash
    pip install -e .
    ```

    For development dependencies (testing, linting), you may need to install additional packages if specified in `pyproject.toml` (currently standard dependencies are sufficient for running tests).

## Running Tests

We use `pytest` for testing. Ensure you have it installed:

```bash
pip install pytest
```

Run the test suite:

```bash
pytest
```

## Writing & Contributing Tests

We aim for enterprise-grade coverage across indexing, embedding, retrieval, and storage layers. Please follow these guidelines when adding or updating tests:

### Where to add tests

- Place new tests in `tests/` using the `test_*.py` naming convention.
- Keep unit tests focused on a single module and use test doubles to isolate external dependencies.
- Add integration-style tests only when they can run reliably in CI (no network, no external DB requirements).

### Test structure & style

- Prefer small, explicit fixtures or inline test doubles over heavy mocks.
- Avoid network calls and real database connections; use in-memory fakes or harnesses.
- Use `pytest` assertions and keep checks explicit and deterministic.

### Coverage expectations

- Exercise core workflows: parsing → indexing → embedding → retrieval.
- Validate edge cases (filters, error handling, and boundary conditions).
- Cover both sync and async execution paths where applicable.

### Running targeted tests

```bash
# Run a single file
pytest tests/test_workflow.py

# Run a single test
pytest tests/test_workflow.py::test_workflow_index_embed_retrieve
```

### Reference scripts

The `tests_files/` directory contains ad-hoc validation scripts used during development.
When a script reveals a regression or important behavior, port it into a `tests/` module
as a deterministic, automated test.

## Project Structure

-   `src/code_graph_indexer`: Main package source code.
    -   `parsing/`: Tree-sitter based parsing logic.
    -   `graph/`: Graph construction and SCIP integration.
    -   `storage/`: Database storage implementations (currently SQLite).
-   `tests/`: Unit and integration tests.

## Guidelines

-   **Code Style**: Please follow standard Python PEP 8 guidelines.
-   **Testing**: Add tests for any new features or bug fixes.
-   **Commits**: Write clear and descriptive commit messages.

## Roadmap

We are currently focused on:
1.  **Indexing**: Robust parsing and graph building.
2.  **Embedding**: Adding vector representations.
3.  **Retrieving**: Implementing retrieval logic.
4.  **Reindexing**: Handling updates.

Check the [Architecture Guide](docs/ARCHITECTURE.md) for a deeper dive into the system design.
