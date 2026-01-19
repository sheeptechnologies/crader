# Roadmap

This page describes the near-term direction for Crader.

## Current bottleneck

SCIP is required to build cross-file relations, but it does not support file-incremental indexing in our pipeline. This makes SCIP the current bottleneck for incremental updates.



## Planned work


- Enable file-incremental semantic indexing.
- Preserve the current storage model and retrieval APIs
- Extend language coverage to all major programming languages.
