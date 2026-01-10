# Installation

This guide covers the prerequisites and steps to install **Crader** in your environment.

## Prerequisites

Before installing the python library, ensure you have the following system components:

1.  **Python 3.10+**: The codebase uses modern Python features like `asyncio` and type hinting.
2.  **PostgreSQL 15+**: Required for robust data storage.
3.  **pgvector**: The PostgreSQL extension for vector similarity search.
4.  **Git**: Required for cloning and managing repositories.
5.  **SCIP CLI** (Required): For advanced semantic indexing (LSIF).
    *   Install via npm: `npm install -g @sourcegraph/scip-typescript @sourcegraph/scip-python` (etc.)

## Installation

### From Source

The recommended way to install is directly from the source code, as it is an internal library.

```bash
git clone https://github.com/your-org/crader.git
cd crader
pip install .
```

### With Development Dependencies

If you plan to contribute or run tests:

```bash
pip install -r requirements.txt
```

## Configuration

The library uses **Environment Variables** for configuration. You can set these in your shell or use a `.env` file.

| Variable | Description | Default | Required |
| :--- | :--- | :--- | :--- |
| `DATABASE_URL` | PostgreSQL Connection String. | `postgresql://user:pass@localhost:5432/sheep` | **Yes** |
| `OPENAI_API_KEY` | Key for generating embeddings (OpenAIProvider). | *None* | **Yes** |
| `REPO_VOLUME` | Local directory where repos are cloned/cached. | `/var/tmp/sheep_volume` | No |
| `LOG_LEVEL` | Python logging level (DEBUG, INFO). | `INFO` | No |

### Setting up PostgreSQL with pgvector

Ensure your database has the `vector` extension enabled:

```sql
CREATE EXTENSION IF NOT EXISTS vector;
```
