# Installation

This guide covers the system requirements and setup steps for Crader.

## Requirements

- Python 3.10+
- PostgreSQL with the pgvector extension
- git
- SCIP CLI tools for cross-file relations (current bottleneck for file-incremental indexing; see [Roadmap](../roadmap.md))
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

## SCIP tooling

SCIP relations require the CLI tools to be installed and available on PATH. This is currently the bottleneck for file-incremental indexing. Install the ones you need for your languages, for example:

```bash
npm install -g @sourcegraph/scip @sourcegraph/scip-python @sourcegraph/scip-typescript
```

Other indexers include `scip-java`, `scip-go`, `scip-rust`, `scip-php`, and `scip-clang`.
