"""initial_schema

Revision ID: c7afc7db3cb4
Revises:
Create Date: 2026-01-25 17:28:23.966354

BREAKING CHANGE: Requires full re-index of all repositories.

Changes from v1:
- Removed: `nodes` table (split into code_chunks + symbols)
- Removed: `edges` table (replaced by symbols.parent_id)
- Added: `code_chunks` table (physical storage for RAG)
- Added: `symbols` table (logical structure for navigation)
- Added: int4range for spatial queries
- Added: partial indexes for query optimization
- Vector index: HNSW (preferred for commit-driven mutable workload)

IMPORTANT: vector_hash computation
- Application must compute: SHA256(enrichment_version + "|" + enriched_text)
- Including 'model' ensures different models produce different hashes
- Cache lookup: WHERE vector_hash = $hash AND model = $model
- See: docs/SCHEMA.md for enrichment strategy

Ref: RFC-002 - Crader Architecture v2
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

revision: str = 'c7afc7db3cb4'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # =================================================================
    # EXTENSIONS
    # =================================================================
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")  # For gen_random_uuid()
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute("CREATE EXTENSION IF NOT EXISTS btree_gist")
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")

    # =================================================================
    # 1. CORE TABLES (Identity & State)
    # =================================================================

    op.create_table(
        'repositories',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False,
                  server_default=sa.text('gen_random_uuid()')),
        sa.Column('name', sa.String(), nullable=False),
        sa.Column('url', sa.String(), nullable=False),
        sa.Column('metadata', postgresql.JSONB(astext_type=sa.Text()), 
                  server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),

        sa.PrimaryKeyConstraint('id'),
    )

    # Trigger for updated_at (scoped naming)
    op.execute("""
        CREATE OR REPLACE FUNCTION crader_update_updated_at()
        RETURNS TRIGGER AS $$
        BEGIN
            NEW.updated_at = NOW();
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """)

    op.execute("""
        CREATE TRIGGER repositories_updated_at
        BEFORE UPDATE ON repositories
        FOR EACH ROW EXECUTE FUNCTION crader_update_updated_at();
    """)

    op.create_table(
        'snapshots',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False,
                  server_default=sa.text('gen_random_uuid()')),
        sa.Column('repository_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('commit_hash', sa.String(), nullable=False),
        sa.Column('metadata', postgresql.JSONB(astext_type=sa.Text()), 
                  server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),

        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['repository_id'], ['repositories.id'], ondelete='CASCADE'),
        sa.UniqueConstraint('repository_id', 'commit_hash', name='uq_snapshots_commit')
    )

    op.create_index('ix_snapshots_repo', 'snapshots', ['repository_id'])

    # =================================================================
    # 2. CONTENT ADDRESSABLE STORAGE (CAS)
    # =================================================================

    op.create_table(
        'contents',
        sa.Column('chunk_hash', sa.String(), nullable=False),
        sa.Column('content', sa.Text(), nullable=False),
        sa.Column('size_bytes', sa.Integer(), nullable=False),

        sa.PrimaryKeyConstraint('chunk_hash'),
        sa.CheckConstraint('size_bytes > 0', name='ck_contents_positive_size')
    )

    # =================================================================
    # 3. FILES
    # =================================================================

    op.create_table(
        'files',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False,
                  server_default=sa.text('gen_random_uuid()')),
        sa.Column('snapshot_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('path', sa.String(), nullable=False),
        sa.Column('language', sa.String(), nullable=False),
        sa.Column('size_bytes', sa.Integer(), nullable=False),
        sa.Column('metadata', postgresql.JSONB(astext_type=sa.Text()), 
                  server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column('indexed_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),

        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['snapshot_id'], ['snapshots.id'], ondelete='CASCADE'),
        sa.UniqueConstraint('snapshot_id', 'path', name='uq_files_snapshot_path'),
        sa.CheckConstraint('size_bytes >= 0', name='ck_files_nonnegative_size')
    )

    op.create_index('ix_files_snapshot', 'files', ['snapshot_id'])
    op.create_index('ix_files_language', 'files', ['language'])
    op.create_index('ix_files_path_pattern', 'files', ['path'],
                   postgresql_using='gin',
                   postgresql_ops={'path': 'gin_trgm_ops'})

    # =================================================================
    # 4. PHYSICAL STORAGE: CODE_CHUNKS (optimized for RAG)
    # =================================================================

    op.create_table(
        'code_chunks',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False,
                  server_default=sa.text('gen_random_uuid()')),
        sa.Column('file_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('chunk_hash', sa.String(), nullable=False),
        sa.Column('byte_range', postgresql.INT4RANGE(), nullable=False),
        sa.Column('metadata', postgresql.JSONB(astext_type=sa.Text()), 
                  server_default=sa.text("'{}'::jsonb"), nullable=False),

        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['file_id'], ['files.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['chunk_hash'], ['contents.chunk_hash'], ondelete='RESTRICT'),
        sa.CheckConstraint('NOT isempty(byte_range) AND lower(byte_range) >= 0',
                          name='ck_chunks_valid_range')
    )

    op.create_index('ix_code_chunks_file', 'code_chunks', ['file_id'])
    op.create_index('ix_code_chunks_hash', 'code_chunks', ['chunk_hash'])
    op.create_index('ix_code_chunks_spatial', 'code_chunks',
                   ['file_id', 'byte_range'],
                   postgresql_using='gist')

    # =================================================================
    # 5. LOGICAL STRUCTURE: SYMBOLS (optimized for navigation)
    # =================================================================

    op.execute("""
        CREATE OR REPLACE FUNCTION crader_qualified_name(scope_path text[], name text)
        RETURNS text
        LANGUAGE sql
        IMMUTABLE
        AS $$
            SELECT CASE
                WHEN array_length(scope_path, 1) > 0
                THEN array_to_string(scope_path, '.') || '.' || name
                ELSE name
            END;
        $$;
    """)

    op.create_table(
        'symbols',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False,
                  server_default=sa.text('gen_random_uuid()')),
        sa.Column('file_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('snapshot_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('kind', sa.String(), nullable=False),
        sa.Column('name', sa.String(), nullable=False),
        sa.Column('scope_path', postgresql.ARRAY(sa.String()), 
                  server_default=sa.text("'{}'::text[]"), nullable=False),
        sa.Column('qualified_name', sa.String(),
                  sa.Computed(
                      "crader_qualified_name(scope_path, name)"
                  ),
                  nullable=False),
        sa.Column('byte_range', postgresql.INT4RANGE(), nullable=False),
        sa.Column('parent_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('metadata', postgresql.JSONB(astext_type=sa.Text()), 
                  server_default=sa.text("'{}'::jsonb"), nullable=False),

        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['file_id'], ['files.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['snapshot_id'], ['snapshots.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['parent_id'], ['symbols.id'], ondelete='CASCADE'),
        sa.CheckConstraint(
            "kind IN ('function', 'class', 'method', 'variable', 'import', 'export')",
            name='ck_symbols_valid_kind'
        ),
        sa.CheckConstraint('NOT isempty(byte_range) AND lower(byte_range) >= 0',
                          name='ck_symbols_valid_range'),

        # NOTE: bulk insert of symbols with parent_id
        # If you insert symbols in a batch where a child arrives before its parent, the foreign key constraint fails.
        # Sort on the application side (recommended — zero database overhead).
        sa.CheckConstraint('parent_id IS NULL OR parent_id != id',
                          name='ck_symbols_no_self_parent')
    )

    # Consistency trigger: enforce snapshot_id matches file's snapshot_id
    op.execute("""
        CREATE OR REPLACE FUNCTION crader_symbols_check_snapshot()
        RETURNS TRIGGER AS $$
        BEGIN
            -- Verify snapshot_id matches the file's snapshot_id
            IF NOT EXISTS (
                SELECT 1 FROM files f 
                WHERE f.id = NEW.file_id 
                  AND f.snapshot_id = NEW.snapshot_id
            ) THEN
                RAISE EXCEPTION 'symbols.snapshot_id must match files.snapshot_id for file_id=%', NEW.file_id;
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """)

    op.execute("""
        CREATE TRIGGER symbols_check_snapshot
        BEFORE INSERT OR UPDATE ON symbols
        FOR EACH ROW EXECUTE FUNCTION crader_symbols_check_snapshot();
    """)

    # Partial indexes
    op.create_index('ix_symbols_defs', 'symbols',
                   ['snapshot_id', 'qualified_name'],
                   postgresql_where=sa.text(
                       "kind IN ('function', 'class', 'method', 'variable')"
                   ))

    op.create_index('ix_symbols_imports', 'symbols',
                   ['snapshot_id', 'name', 'file_id'],
                   postgresql_where=sa.text("kind = 'import'"))

    op.create_index('ix_symbols_parent', 'symbols', ['parent_id'],
                   postgresql_where=sa.text("parent_id IS NOT NULL"))

    # Spatial index
    op.create_index('ix_symbols_spatial', 'symbols',
                   ['file_id', 'byte_range'],
                   postgresql_using='gist')

    # Standard indexes
    op.create_index('ix_symbols_snapshot', 'symbols', ['snapshot_id'])
    op.create_index('ix_symbols_file', 'symbols', ['file_id'])
    op.create_index('ix_symbols_name', 'symbols', ['snapshot_id', 'name'])

    # =================================================================
    # 6. SEMANTIC VECTORS: CODE_CHUNK_EMBEDDINGS
    # =================================================================

    op.create_table(
        'code_chunk_embeddings',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False,
                  server_default=sa.text('gen_random_uuid()')),
        sa.Column('chunk_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('snapshot_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('file_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('vector_hash', sa.String(), nullable=False),
        sa.Column('embedding', Vector(1536), nullable=False),
        sa.Column('model', sa.String(), nullable=False),

        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['chunk_id'], ['code_chunks.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['snapshot_id'], ['snapshots.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['file_id'], ['files.id'], ondelete='CASCADE'),
    )

    # Consistency trigger: enforce snapshot_id and file_id match chunk's file
    op.execute("""
        CREATE OR REPLACE FUNCTION crader_embeddings_check_refs()
        RETURNS TRIGGER AS $$
        BEGIN
            -- Verify snapshot_id and file_id match the chunk's file
            IF NOT EXISTS (
                SELECT 1 
                FROM code_chunks c
                JOIN files f ON f.id = c.file_id
                WHERE c.id = NEW.chunk_id
                  AND c.file_id = NEW.file_id
                  AND f.snapshot_id = NEW.snapshot_id
            ) THEN
                RAISE EXCEPTION 'embeddings snapshot_id/file_id must match chunk''s file';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """)

    op.execute("""
        CREATE TRIGGER embeddings_check_refs
        BEFORE INSERT OR UPDATE ON code_chunk_embeddings
        FOR EACH ROW EXECUTE FUNCTION crader_embeddings_check_refs();
    """)

    # NOTE: vector_hash is a global cache key for embedding reuse (compute-dedup).
    # We may store the same embedding multiple times across chunks/snapshots to keep
    # vector search filterable by snapshot_id (no joins).
    
    # Cache lookup index: composite (vector_hash, model)
    # Query pattern: SELECT embedding WHERE vector_hash = $hash AND model = $model
    op.create_index(
        'ix_embeddings_cache_key',
        'code_chunk_embeddings',
        ['vector_hash', 'model']
    )
    
    # Idempotency: prevent duplicate embeddings for same chunk+model
    # Protects against re-indexing, retries, or job crashes
    op.create_index(
        'uq_embeddings_chunk_model',
        'code_chunk_embeddings',
        ['chunk_id', 'model'],
        unique=True
    )
  
    # Standard indexes for filtering
    op.create_index('ix_embeddings_snapshot', 'code_chunk_embeddings', ['snapshot_id'])
    op.create_index('ix_embeddings_file', 'code_chunk_embeddings', ['file_id'])

    # Vector index: HNSW (preferred for commit-driven mutable workload)
    # Rationale: Crader's embedding workload is snapshot-based with frequent
    # insert/delete cycles and bounded size via GC. HNSW handles this better
    # than IVFFLAT, which requires periodic rebuilds for optimal recall.
    # See: docs/VECTOR_INDEX_STRATEGY.md
    op.create_index(
        'ix_embeddings_vector',
        'code_chunk_embeddings',
        ['embedding'],
        postgresql_using='hnsw',
        postgresql_with={'m': 16, 'ef_construction': 64},
        postgresql_ops={'embedding': 'vector_cosine_ops'}
    )

    # =================================================================
    # 7. LEXICAL SEARCH: CODE_CHUNK_FTS
    # =================================================================

    op.create_table(
        'code_chunk_fts',
        sa.Column('chunk_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('snapshot_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('file_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('search_vector', postgresql.TSVECTOR(), nullable=False),

        sa.PrimaryKeyConstraint('chunk_id'),
        sa.ForeignKeyConstraint(['chunk_id'], ['code_chunks.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['snapshot_id'], ['snapshots.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['file_id'], ['files.id'], ondelete='CASCADE'),
    )

    op.create_index('ix_fts_vector', 'code_chunk_fts', ['search_vector'], 
                   postgresql_using='gin')
    op.create_index('ix_fts_snapshot', 'code_chunk_fts', ['snapshot_id'])
    op.create_index('ix_fts_file', 'code_chunk_fts', ['file_id'])

    # FTS Trigger: automatically populate FTS table when chunks are inserted
    # NOTE: Uses 'simple' config instead of 'english' to avoid stemming code
    # English stemming (e.g., "running" -> "run") is inappropriate for code.
    # 'simple' does basic tokenization without language-specific processing.
    op.execute("""
        CREATE OR REPLACE FUNCTION crader_update_fts_vector()
        RETURNS TRIGGER AS $$
        BEGIN
            INSERT INTO code_chunk_fts (chunk_id, snapshot_id, file_id, search_vector)
            SELECT 
                NEW.id,
                f.snapshot_id,
                NEW.file_id,
                to_tsvector('simple', co.content)
            FROM contents co
            JOIN files f ON f.id = NEW.file_id
            WHERE co.chunk_hash = NEW.chunk_hash
            ON CONFLICT (chunk_id) DO UPDATE
            SET search_vector = EXCLUDED.search_vector,
                snapshot_id = EXCLUDED.snapshot_id,
                file_id = EXCLUDED.file_id;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """)

    op.execute("""
        CREATE TRIGGER code_chunks_fts_trigger
        AFTER INSERT OR UPDATE ON code_chunks
        FOR EACH ROW EXECUTE FUNCTION crader_update_fts_vector();
    """)


def downgrade() -> None:
    # Drop triggers and functions first
    op.execute("DROP TRIGGER IF EXISTS code_chunks_fts_trigger ON code_chunks")
    op.execute("DROP FUNCTION IF EXISTS crader_update_fts_vector()")
    
    op.execute("DROP TRIGGER IF EXISTS embeddings_check_refs ON code_chunk_embeddings")
    op.execute("DROP FUNCTION IF EXISTS crader_embeddings_check_refs()")
    
    op.execute("DROP TRIGGER IF EXISTS symbols_check_snapshot ON symbols")
    op.execute("DROP FUNCTION IF EXISTS crader_symbols_check_snapshot()")
    op.execute("DROP FUNCTION IF EXISTS crader_qualified_name(text[], text)")
    
    op.execute("DROP TRIGGER IF EXISTS repositories_updated_at ON repositories")
    op.execute("DROP FUNCTION IF EXISTS crader_update_updated_at()")

    # Drop tables in reverse order
    op.drop_table('code_chunk_fts')
    op.drop_table('code_chunk_embeddings')
    op.drop_table('symbols')
    op.drop_table('code_chunks')
    op.drop_table('files')
    op.drop_table('contents')
    op.drop_table('snapshots')
    op.drop_table('repositories')

    # Drop extensions
    op.execute("DROP EXTENSION IF EXISTS pg_trgm")
    op.execute("DROP EXTENSION IF EXISTS btree_gist")
    op.execute("DROP EXTENSION IF EXISTS vector")
    op.execute("DROP EXTENSION IF EXISTS pgcrypto")
