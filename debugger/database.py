import os
from src.code_graph_indexer.storage.postgres import PostgresGraphStorage

# Default to the one used in tests, but allow env override
DB_URL = os.getenv("SHEEP_DB_URL", "postgresql://sheep_user:sheep_password@localhost:5433/sheep_index")

import psycopg

_storage_instance = None

class SafePostgresGraphStorage(PostgresGraphStorage):
    def _init_schema(self):
        with self.pool.connection() as conn:
            with conn.cursor() as cur:
                try:
                    cur.execute("CREATE EXTENSION IF NOT EXISTS vector")
                except (psycopg.errors.UniqueViolation, psycopg.errors.DuplicateObject):
                    pass

                try:
                    cur.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
                except (psycopg.errors.UniqueViolation, psycopg.errors.DuplicateObject):
                    pass
                
                # Helper to safely execute DDL
                def safe_execute(query, params=None):
                    try:
                        cur.execute(query, params)
                    except (psycopg.errors.UniqueViolation, psycopg.errors.DuplicateObject):
                        pass
                    except psycopg.errors.InFailedSqlTransaction:
                        # If we are in a failed transaction, we can't do anything.
                        # But we are in autocommit=True mode usually? 
                        # PostgresGraphStorage uses autocommit=True.
                        pass

                safe_execute("""
                    CREATE TABLE IF NOT EXISTS repositories (
                        id UUID PRIMARY KEY,
                        url TEXT NOT NULL, branch TEXT NOT NULL,
                        name TEXT, last_commit TEXT, status TEXT, updated_at TIMESTAMP, local_path TEXT,
                        queued_commit TEXT,
                        UNIQUE(url, branch)
                    )
                """)

                safe_execute("""
                    CREATE TABLE IF NOT EXISTS files (
                        id UUID PRIMARY KEY,
                        repo_id UUID REFERENCES repositories(id),
                        commit_hash TEXT, file_hash TEXT,
                        path TEXT, language TEXT, size_bytes BIGINT, category TEXT, indexed_at TIMESTAMP,
                        parsing_status TEXT DEFAULT 'success', parsing_error TEXT,
                        UNIQUE(repo_id, path)
                    )
                """)
                safe_execute("CREATE INDEX IF NOT EXISTS idx_files_repo ON files (repo_id)")
                safe_execute("CREATE INDEX IF NOT EXISTS idx_files_path ON files (path)")
                safe_execute("CREATE INDEX IF NOT EXISTS idx_files_status ON files (parsing_status)")

                safe_execute("""
                    CREATE TABLE IF NOT EXISTS nodes (
                        id UUID PRIMARY KEY,
                        file_id UUID REFERENCES files(id) ON DELETE CASCADE,
                        file_path TEXT,
                        start_line INT, end_line INT, 
                        byte_start INT, byte_end INT,
                        chunk_hash TEXT, size INT,
                        metadata JSONB 
                    )
                """)
                safe_execute("CREATE INDEX IF NOT EXISTS idx_nodes_file ON nodes (file_id)")
                safe_execute("CREATE INDEX IF NOT EXISTS idx_nodes_meta ON nodes USING GIN (metadata)")

                safe_execute("CREATE TABLE IF NOT EXISTS contents (chunk_hash TEXT PRIMARY KEY, content TEXT)")
                
                safe_execute("""
                    CREATE TABLE IF NOT EXISTS edges (
                        source_id UUID, target_id UUID, relation_type TEXT, metadata JSONB
                    )
                """)
                safe_execute("CREATE INDEX IF NOT EXISTS idx_edges_src ON edges (source_id)")
                safe_execute("CREATE INDEX IF NOT EXISTS idx_edges_tgt ON edges (target_id)")

                safe_execute("""
                    CREATE TABLE IF NOT EXISTS nodes_fts (
                        node_id UUID PRIMARY KEY REFERENCES nodes(id) ON DELETE CASCADE,
                        file_path TEXT, semantic_tags TEXT, content TEXT, search_vector TSVECTOR
                    )
                """)
                safe_execute("CREATE INDEX IF NOT EXISTS idx_fts_vec ON nodes_fts USING GIN (search_vector)")

                safe_execute(f"""
                    CREATE TABLE IF NOT EXISTS node_embeddings (
                        id UUID PRIMARY KEY,
                        chunk_id UUID REFERENCES nodes(id) ON DELETE CASCADE,
                        repo_id UUID,
                        file_path TEXT, branch TEXT, language TEXT, category TEXT,
                        start_line INT, end_line INT,
                        vector_hash TEXT, model_name TEXT, created_at TIMESTAMP,
                        embedding VECTOR({self.vector_dim})
                    )
                """)
                safe_execute("""
                    CREATE INDEX IF NOT EXISTS idx_emb_vector 
                    ON node_embeddings USING hnsw (embedding vector_cosine_ops)
                """)
                safe_execute("CREATE INDEX IF NOT EXISTS idx_emb_repo ON node_embeddings (repo_id)")

        self.pool.close()
        self._create_pool()

def get_storage():
    """
    Returns a singleton instance of the storage.
    """
    global _storage_instance
    if _storage_instance is None:
        _storage_instance = SafePostgresGraphStorage(DB_URL, vector_dim=1536)
    return _storage_instance
