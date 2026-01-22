# Installation

This guide covers the system requirements and setup steps for Crader.

## Requirements

- Python 3.10+
- PostgreSQL with the pgvector extension
- git
- Optional: OpenAI API key if you use OpenAI embeddings

## Install the package

```bash
pip install crader
```

## Database setup

Set your database URL and run migrations:

```bash
export CRADER_DB_URL="postgresql://user:pass@localhost:5432/codebase"
crader db upgrade
```

The migration enables the `vector` extension and creates all required tables.

## Environment variables

- `CRADER_DB_URL`: PostgreSQL connection string (required by CLI and `CodebaseIndexer`).
- `CRADER_REPO_VOLUME`: Root directory for cached repos and worktrees (defaults to `./sheep_data/repositories`).
- `CRADER_OPENAI_API_KEY` or `OPENAI_API_KEY`: OpenAI credentials for embeddings.
