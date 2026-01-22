# Roadmap

This page describes the near-term direction for Crader.

## Current status

Crader uses Tree-sitter for parsing and chunking, enabling true file-incremental indexing. The indexer only processes changed files when re-indexing a repository.

## Planned work

- Extend semantic tagging queries to all supported languages.
- Preserve the current storage model and retrieval APIs.
- Add support for additional programming languages.
- Improve chunking strategies for large files.
