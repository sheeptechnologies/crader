# Roadmap

This page describes the near-term direction for Crader.

## Current bottleneck

SCIP is required to build cross-file relations, but it does not support file-incremental indexing in our pipeline. This makes SCIP the current bottleneck for incremental updates.

## Mycelium and stack graphs

We are developing **Mycelium** (https://github.com/sheeptechnologies/mycelium.git), a Python implementation of GitHub-style stack graphs built on Tree-sitter. The goal is to replace SCIP in Crader with a lighter, controllable semantic indexing backend that supports incremental resolution.

## Planned work

- Integrate Mycelium as the cross-file relation backend.
- Enable file-incremental semantic indexing.
- Preserve the current storage model and retrieval APIs while swapping the relation extractor.
- Stabilize the Mycelium API and language coverage needed by Crader.
- Extend language coverage to all major programming languages.
