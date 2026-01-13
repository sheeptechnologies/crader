# Embedding strategy

Crader generates embeddings in a separate step using `CodebaseIndexer.embed()` and `CodeEmbedder`. The pipeline is staged and deduplicated to reduce API calls.

## Pipeline summary

1. **Staging**
   - Nodes that are missing embeddings are streamed from the database.
   - Each node is transformed into a prompt and hashed into a `vector_hash`.
   - Rows are bulk loaded into the `staging_embeddings` table.

2. **Deduplication**
   - `vector_hash` is matched against existing embeddings in `node_embeddings`.
   - If a match exists, the vector is copied into staging.

3. **Delta processing**
   - Remaining rows are fetched in batches.
   - Workers call the embedding provider and write vectors back to the database.

4. **Promotion and cleanup**
   - Staged rows with embeddings are promoted into `node_embeddings`.
   - Staging rows are removed.

## Prompt template

`CodeEmbedder` embeds a structured prompt that combines file metadata and code:

```
[CONTEXT]
File: <file_path>
Language: <language>
Category: <category>
Role: <role tags>
Tags: <other semantic tags>
Defines: <incoming definitions>

[CODE]
<chunk content>
```

The `vector_hash` is the SHA-256 hash of this full prompt. Moving a file or changing metadata will change the hash.

## Providers

Crader ships with these embedding providers:

- `OpenAIEmbeddingProvider` (uses `CRADER_OPENAI_API_KEY` or `OPENAI_API_KEY`)
- `FastEmbedProvider` (local embeddings via `fastembed`)
- `DummyEmbeddingProvider` (random vectors for tests)

You can implement the `EmbeddingProvider` interface to integrate another provider.
