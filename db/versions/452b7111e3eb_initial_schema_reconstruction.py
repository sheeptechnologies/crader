"""initial_schema_reconstruction

Revision ID: 452b7111e3eb
Revises: 
Create Date: 2025-12-06 13:05:18.442144

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from pgvector.sqlalchemy import Vector

# revision identifiers, used by Alembic.
revision: str = '<ID_GENERATO_DA_ALEMBIC>' # NON TOCCARE QUELLO CHE C'È GIÀ NEL FILE
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Abilita Estensioni
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")

    # 2. Tabella Repositories
    op.create_table(
        'repositories',
        sa.Column('id', sa.UUID(), primary_key=True),
        sa.Column('url', sa.Text(), nullable=False),
        sa.Column('branch', sa.Text(), nullable=False),
        sa.Column('name', sa.Text(), nullable=True),
        sa.Column('last_commit', sa.Text(), nullable=True),
        sa.Column('status', sa.Text(), nullable=True),
        sa.Column('updated_at', sa.TIMESTAMP(), nullable=True),
        sa.Column('local_path', sa.Text(), nullable=True),
        sa.Column('queued_commit', sa.Text(), nullable=True),
        sa.UniqueConstraint('url', 'branch', name='uq_repo_url_branch')
    )

    # 3. Tabella Files
    op.create_table(
        'files',
        sa.Column('id', sa.UUID(), primary_key=True),
        sa.Column('repo_id', sa.UUID(), sa.ForeignKey('repositories.id'), nullable=True),
        sa.Column('commit_hash', sa.Text(), nullable=True),
        sa.Column('file_hash', sa.Text(), nullable=True),
        sa.Column('path', sa.Text(), nullable=True),
        sa.Column('language', sa.Text(), nullable=True),
        sa.Column('size_bytes', sa.BigInteger(), nullable=True),
        sa.Column('category', sa.Text(), nullable=True),
        sa.Column('indexed_at', sa.TIMESTAMP(), nullable=True),
        sa.Column('parsing_status', sa.Text(), server_default='success', nullable=True),
        sa.Column('parsing_error', sa.Text(), nullable=True),
        sa.UniqueConstraint('repo_id', 'path', name='uq_files_repo_path')
    )
    op.create_index('idx_files_repo', 'files', ['repo_id'])
    op.create_index('idx_files_path', 'files', ['path'])
    op.create_index('idx_files_status', 'files', ['parsing_status'])

    # 4. Tabella Nodes
    op.create_table(
        'nodes',
        sa.Column('id', sa.UUID(), primary_key=True),
        sa.Column('file_id', sa.UUID(), sa.ForeignKey('files.id', ondelete='CASCADE'), nullable=True),
        sa.Column('file_path', sa.Text(), nullable=True),
        sa.Column('start_line', sa.Integer(), nullable=True),
        sa.Column('end_line', sa.Integer(), nullable=True),
        sa.Column('byte_start', sa.Integer(), nullable=True),
        sa.Column('byte_end', sa.Integer(), nullable=True),
        sa.Column('chunk_hash', sa.Text(), nullable=True),
        sa.Column('size', sa.Integer(), nullable=True),
        sa.Column('metadata', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.create_index('idx_nodes_file', 'nodes', ['file_id'])
    # Indice GIN per JSONB
    op.execute("CREATE INDEX IF NOT EXISTS idx_nodes_meta ON nodes USING GIN (metadata)")

    # 5. Tabella Contents
    op.create_table(
        'contents',
        sa.Column('chunk_hash', sa.Text(), primary_key=True),
        sa.Column('content', sa.Text(), nullable=True)
    )

    # 6. Tabella Edges
    op.create_table(
        'edges',
        sa.Column('source_id', sa.UUID(), nullable=True),
        sa.Column('target_id', sa.UUID(), nullable=True),
        sa.Column('relation_type', sa.Text(), nullable=True),
        sa.Column('metadata', postgresql.JSONB(astext_type=sa.Text()), nullable=True)
    )
    op.create_index('idx_edges_src', 'edges', ['source_id'])
    op.create_index('idx_edges_tgt', 'edges', ['target_id'])

    # 7. Tabella Nodes FTS (Full Text Search)
    op.create_table(
        'nodes_fts',
        sa.Column('node_id', sa.UUID(), sa.ForeignKey('nodes.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('file_path', sa.Text(), nullable=True),
        sa.Column('semantic_tags', sa.Text(), nullable=True),
        sa.Column('content', sa.Text(), nullable=True),
        sa.Column('search_vector', postgresql.TSVECTOR(), nullable=True)
    )
    op.create_index('idx_fts_vec', 'nodes_fts', ['search_vector'], postgresql_using='gin')

    # 8. Tabella Node Embeddings
    # NOTA: Assumiamo vector_dim = 1536 come default. 
    # In scenari avanzati la dimensione potrebbe dover essere dinamica, ma per uno schema SQL rigido si fissa qui.
    op.create_table(
        'node_embeddings',
        sa.Column('id', sa.UUID(), primary_key=True),
        sa.Column('chunk_id', sa.UUID(), sa.ForeignKey('nodes.id', ondelete='CASCADE'), nullable=True),
        sa.Column('repo_id', sa.UUID(), nullable=True),
        sa.Column('file_path', sa.Text(), nullable=True),
        sa.Column('branch', sa.Text(), nullable=True),
        sa.Column('language', sa.Text(), nullable=True),
        sa.Column('category', sa.Text(), nullable=True),
        sa.Column('start_line', sa.Integer(), nullable=True),
        sa.Column('end_line', sa.Integer(), nullable=True),
        sa.Column('vector_hash', sa.Text(), nullable=True),
        sa.Column('model_name', sa.Text(), nullable=True),
        sa.Column('created_at', sa.TIMESTAMP(), nullable=True),
        sa.Column('embedding', Vector(1536), nullable=True)
    )
    op.create_index('idx_emb_repo', 'node_embeddings', ['repo_id'])
    # Indice HNSW per i vettori
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_emb_vector 
        ON node_embeddings USING hnsw (embedding vector_cosine_ops)
    """)


def downgrade() -> None:
    # Ordine inverso per evitare errori di Foreign Key
    op.execute("DROP INDEX IF EXISTS idx_emb_vector")
    op.drop_table('node_embeddings')
    op.drop_table('nodes_fts')
    op.drop_table('edges')
    op.drop_table('contents')
    op.drop_index('idx_nodes_meta', table_name='nodes')
    op.drop_table('nodes')
    op.drop_table('files')
    op.drop_table('repositories')
    op.execute("DROP EXTENSION IF EXISTS vector")
    op.execute("DROP EXTENSION IF EXISTS pg_trgm")