# Development setup

This guide describes a minimal local setup for working on Crader.

## Requirements

- Python 3.10+
- git
- Optional: PostgreSQL with pgvector (required for e2e tests)
- SCIP tools for full relation extraction (currently the bottleneck for file-incremental indexing; see [Roadmap](../roadmap.md) and https://github.com/sheeptechnologies/mycelium.git)

## Setup

```bash
git clone https://github.com/filippodaminato/crader.git
cd crader

python -m venv .venv
source .venv/bin/activate

pip install -e ".[dev]"
```

## Tests

Unit and integration tests are mostly mocked and do not require a database:

```bash
pytest tests/unit/ tests/integration/
```

End-to-end tests require PostgreSQL and a runnable git environment:

```bash
pytest tests/e2e/
```

## Linting and type checks

```bash
ruff check src tests
mypy src
```

## Local PostgreSQL (optional)

A Docker Compose file is available for the debugger stack:

```bash
docker compose -f tools/debugger/docker-compose.yml up -d
```
