"""initial_schema

Revision ID: c7afc7db3cb4
Revises:
Create Date: 2025-12-07 17:28:23.966354

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'c7afc7db3cb4'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Abilitazione estensione vector (fondamentale per gli embeddings)
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    # =================================================================
    # 1. CORE TABLES (Identity & State)
    # =================================================================

    # REPOSITORIES: L'identità stabile del progetto
    # Nota: current_snapshot_id punta a snapshots, ma lo definiamo dopo per evitare cicli
    op.create_table(
        'repositories',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('url', sa.String(), nullable=False),
        sa.Column('branch', sa.String(), nullable=False),
        sa.Column('name', sa.String(), nullable=False),

        sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        # Questo campo verrà collegato via FK alla fine dello script
        sa.Column('current_snapshot_id', sa.String(), nullable=True),
        # Se valorizzato, significa "qualcuno ha chiesto un update mentre eri occupato"
        sa.Column('reindex_requested_at', sa.DateTime(), nullable=True),

        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('url', 'branch', name='uq_repo_url_branch')
        # Impedisce fisicamente di avere due snapshot 'indexing' contemporaneamente per la stessa repo.

    )



    # SNAPSHOTS: Lo stato immutabile (Versioni)
    op.create_table(
        'snapshots',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('repository_id', sa.String(), nullable=False),
        sa.Column('commit_hash', sa.String(), nullable=False),
        sa.Column(
            'status', sa.String(), server_default='pending', nullable=False
        ),  # pending, indexing, completed, failed
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.Column('completed_at', sa.DateTime(), nullable=True),
        sa.Column('stats', postgresql.JSONB(astext_type=sa.Text()), server_default='{}', nullable=True),
        sa.Column('file_manifest', postgresql.JSONB(astext_type=sa.Text()), server_default='{}', nullable=True),

        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['repository_id'], ['repositories.id'], ondelete='CASCADE'),
        # Garantisce Idempotenza: Non possono esistere due snapshot per lo stesso commit
        # sa.UniqueConstraint('repository_id', 'commit_hash', name='uq_snapshot_repo_commit')
        # rimosso per dinamiche di reindex force=True
    )

    # Ora possiamo aggiungere la FK circolare su repositories
    op.create_foreign_key(
        'fk_repo_current_snapshot',
        'repositories', 'snapshots',
        ['current_snapshot_id'], ['id'],
        ondelete='SET NULL',  # Se cancello lo snapshot, la repo torna "vergine"
        use_alter=True
    )

    # Questo delega la gestione della concorrenza a Postgres (molto robusto).
    op.create_index(
        'ix_one_active_indexing',
        'snapshots',
        ['repository_id'],
        unique=True,
        postgresql_where=sa.text("status = 'indexing'") # Partial Index
    )

    # =================================================================
    # 2. CONTENT ADDRESSABLE STORAGE (CAS)
    # =================================================================

    # CONTENTS: Deduplicazione globale dei contenuti (Blob store)
    op.create_table(
        'contents',
        sa.Column('chunk_hash', sa.String(), nullable=False),
        sa.Column('content', sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint('chunk_hash')
    )

    # =================================================================
    # 3. STRUCTURE (AST & Files)
    # =================================================================

    # FILES: Appartengono a uno Snapshot specifico
    op.create_table(
        'files',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('snapshot_id', sa.String(), nullable=False),
        sa.Column('path', sa.String(), nullable=False),
        sa.Column('file_hash', sa.String(), nullable=False),
        sa.Column('commit_hash', sa.String(), nullable=True), # Utile per riferimento rapido
        sa.Column('language', sa.String(), nullable=False),
        sa.Column('size_bytes', sa.Integer(), nullable=False),
        sa.Column('category', sa.String(), nullable=False), # test, source, config...
        sa.Column('indexed_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.Column('parsing_status', sa.String(), server_default='success', nullable=False),
        sa.Column('parsing_error', sa.Text(), nullable=True),

        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['snapshot_id'], ['snapshots.id'], ondelete='CASCADE'),
        sa.UniqueConstraint('snapshot_id', 'path', name='uq_files_snapshot_path')
    )

    # NODES: I mattoncini del codice (Classi, Funzioni, Blocchi)
    op.create_table(
        'nodes',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('file_id', sa.String(), nullable=False),
        sa.Column('file_path', sa.String(), nullable=False), # Denormalizzato per comodità
        sa.Column('chunk_hash', sa.String(), nullable=False), # Link al CAS
        sa.Column('start_line', sa.Integer(), nullable=False),
        sa.Column('end_line', sa.Integer(), nullable=False),
        sa.Column('byte_start', sa.Integer(), nullable=False),
        sa.Column('byte_end', sa.Integer(), nullable=False),
        sa.Column('size', sa.Integer(), nullable=False),
        sa.Column('metadata', postgresql.JSONB(astext_type=sa.Text()), server_default='{}', nullable=True),

        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['file_id'], ['files.id'], ondelete='CASCADE'),
        # Non mettiamo FK su chunk_hash verso contents per performance in insert massivi (è un soft link)
    )

    # EDGES: Le relazioni del grafo (calls, inherits, imports...)
    op.create_table(
        'edges',
        sa.Column('source_id', sa.String(), nullable=False),
        sa.Column('target_id', sa.String(), nullable=False),
        sa.Column('relation_type', sa.String(), nullable=False),
        sa.Column('metadata', postgresql.JSONB(astext_type=sa.Text()), server_default='{}', nullable=True),

        sa.ForeignKeyConstraint(['source_id'], ['nodes.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['target_id'], ['nodes.id'], ondelete='CASCADE')
    )
    # Indici per navigazione veloce grafo
    op.create_index('ix_edges_source', 'edges', ['source_id'])
    op.create_index('ix_edges_target', 'edges', ['target_id'])

    # =================================================================
    # 4. SEARCH INDICES (Vectors & FTS)
    # =================================================================

    # NODE_EMBEDDINGS: Vettori semantici
    # NOTA: Qui denormalizziamo snapshot_id per query ultra-veloci
    op.create_table(
        'node_embeddings',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('chunk_id', sa.String(), nullable=False),
        sa.Column('snapshot_id', sa.String(), nullable=False), # Denormalizzato
        sa.Column('vector_hash', sa.String(), nullable=False), # Per cache lookup
        sa.Column('model_name', sa.String(), nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        # Campi denormalizzati utili per filtering pre-vettoriale senza JOIN pesanti
        sa.Column('file_path', sa.String(), nullable=True),
        sa.Column('language', sa.String(), nullable=True),
        sa.Column('category', sa.String(), nullable=True),
        sa.Column('start_line', sa.Integer(), nullable=True),
        sa.Column('end_line', sa.Integer(), nullable=True),

        # Il vettore vero e proprio (dimensione standard OpenAI 1536)
        sa.Column('embedding', Vector(1536), nullable=True),

        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['chunk_id'], ['nodes.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['snapshot_id'], ['snapshots.id'], ondelete='CASCADE')
    )
    # Indice HNSW partizionato per snapshot sarebbe ideale, ma iniziamo con indice standard su embedding
    # e indice btree su snapshot_id per il filtering.
    op.create_index('ix_embeddings_snapshot', 'node_embeddings', ['snapshot_id'])
    op.create_index('ix_node_embeddings_vector_hash', 'node_embeddings', ['vector_hash'])
    # op.create_index(
    #     'ix_embeddings_vector',
    #     'node_embeddings',
    #     ['embedding'],
    #     postgresql_using='hnsw',
    #     postgresql_ops={'embedding': 'vector_cosine_ops'}
    # )
    # (L'indice HNSW spesso si crea manualmente post-data load per performance, o si lascia qui se il DB è piccolo)

    # NODES_FTS: Full Text Search
    op.create_table(
        'nodes_fts',
        sa.Column('node_id', sa.String(), nullable=False),
        sa.Column('file_path', sa.String(), nullable=False),
        sa.Column('content', sa.Text(), nullable=True),
        sa.Column('semantic_tags', sa.Text(), nullable=True),
        sa.Column('search_vector', postgresql.TSVECTOR(), nullable=True),

        sa.PrimaryKeyConstraint('node_id'),
        sa.ForeignKeyConstraint(['node_id'], ['nodes.id'], ondelete='CASCADE')
    )
    op.create_index('ix_nodes_fts_vector', 'nodes_fts', ['search_vector'], postgresql_using='gin')


def downgrade() -> None:
    # Ordine inverso di drop per rispettare le FK
    op.drop_table('nodes_fts')
    op.drop_table('node_embeddings')
    op.drop_table('edges')
    op.drop_table('nodes')
    op.drop_table('files')
    op.drop_table('contents')

    # Rimuovi FK circolare prima di droppare le tabelle
    op.drop_constraint('fk_repo_current_snapshot', 'repositories', type_='foreignkey')

    op.drop_table('snapshots')
    op.drop_table('repositories')

    op.execute("DROP EXTENSION IF EXISTS vector")
