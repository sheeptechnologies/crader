# Development Setup

This guide covers how to set up the development environment for contributing to **Crader**.

## Prerequisites

*   **Python**: Version 3.11 or higher.
*   **Docker**: Required for running the PostgreSQL + pgvector integration tests.
*   **Node.js**: Required if you plan to touch the `debugger/frontend`.
*   **SCIP Tools**: To run full end-to-end indexing on local code.

## 1. Environment Setup

We recommend using `venv` or `poetry`.

```bash
# Clone the repository
git clone https://github.com/filippodaminato/crader.git
cd crader

# Create virtual env
python -m venv .venv
source .venv/bin/activate

# Install dependencies (Editable mode + Dev tools)
pip install -e ".[dev]"
```

## 2. Infrastructure (Postgres)

Start the local database for testing.

```bash
docker-compose up -d db
```

This starts PostgreSQL on port `6432` (mapped) with `pgvector` enabled.
Connection String: `postgresql://sheep_user:sheep_password@localhost:6432/sheep_index`

## 3. Running Tests

We use `pytest`.

```bash
# Run all tests
pytest tests/

# Run specific functional tests (slow, integration)
pytest tests_files/test_workflow.py
```

## 4. Code Style

*   **Linting**: We use `ruff`.
*   **Formatting**: We use `black`.
*   **Type Checking**: We use `mypy`.

```bash
# Run full check
ruff check .
black .
mypy src/
```
