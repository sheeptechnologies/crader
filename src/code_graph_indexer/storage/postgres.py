import os
import json
import logging
import struct
import datetime
import uuid
from typing import List, Dict, Any, Optional, Generator, Tuple
import psycopg
from psycopg.rows import dict_row
from pgvector.psycopg import register_vector
from psycopg_pool import ConnectionPool

from .base import GraphStorage

logger = logging.getLogger(__name__)

class PostgresGraphStorage(GraphStorage):
    def __init__(self, db_url: str, min_size: int = 4, max_size: int = 20, vector_dim: int = 1536):
        """
        Storage basato su PostgreSQL con Connection Pooling.
        """
        self._db_url = db_url
        self.vector_dim = vector_dim
        
        # Salviamo i parametri per poter ricreare il pool
        self._min_size = min_size
        self._max_size = max_size
        self._pool_kwargs = {
            "row_factory": dict_row,
            "autocommit": True 
        }
        
        safe_url = db_url.split('@')[-1] if '@' in db_url else "..."
        logger.info(f"🐘 Connecting to Postgres (Pool): {safe_url} | Vector Dim: {vector_dim}")
        
        # 1. Creiamo il pool iniziale
        self._create_pool()
        
        # 2. Inizializziamo lo schema (che resetterà il pool alla fine)
        self._init_schema()

    def _create_pool(self):
        """Crea una nuova istanza del Connection Pool."""
        self.pool = ConnectionPool(
            conninfo=self._db_url,
            min_size=self._min_size,
            max_size=self._max_size,
            kwargs=self._pool_kwargs,
            configure=self._configure_connection
        )
        self.pool.wait()

    def _configure_connection(self, conn: psycopg.Connection):
        """Callback eseguita su ogni nuova connessione creata dal pool."""
        try:
            register_vector(conn)
        except psycopg.ProgrammingError:
            pass # Vector non esiste ancora (primo avvio)

    def close(self):
        """Chiude il pool e tutte le connessioni."""
        if hasattr(self, 'pool') and self.pool:
            self.pool.close()
            logger.info("🐘 Postgres Pool closed.")

    def commit(self):
        """Stub per compatibilità."""
        pass

    def _init_schema(self):
        """Crea tabelle ed estensioni."""
        # Usiamo il pool corrente per creare le tabelle
        with self.pool.connection() as conn:
            with conn.cursor() as cur:
                # 1. Estensioni
                cur.execute("CREATE EXTENSION IF NOT EXISTS vector")
                cur.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
                
                # 2. Repositories
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS repositories (
                        id UUID PRIMARY KEY,
                        url TEXT NOT NULL, branch TEXT NOT NULL,
                        name TEXT, last_commit TEXT, status TEXT, updated_at TIMESTAMP, local_path TEXT,
                        UNIQUE(url, branch)
                    )
                """)

                # 3. Files
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS files (
                        id UUID PRIMARY KEY,
                        repo_id UUID REFERENCES repositories(id),
                        commit_hash TEXT, file_hash TEXT,
                        path TEXT, language TEXT, size_bytes BIGINT, category TEXT, indexed_at TIMESTAMP,
                        UNIQUE(repo_id, path)
                    )
                """)
                cur.execute("CREATE INDEX IF NOT EXISTS idx_files_repo ON files (repo_id)")
                cur.execute("CREATE INDEX IF NOT EXISTS idx_files_path ON files (path)")

                # 4. Nodes (JSONB Metadata)
                cur.execute("""
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
                cur.execute("CREATE INDEX IF NOT EXISTS idx_nodes_file ON nodes (file_id)")
                cur.execute("CREATE INDEX IF NOT EXISTS idx_nodes_meta ON nodes USING GIN (metadata)")

                # 5. Contents
                cur.execute("CREATE TABLE IF NOT EXISTS contents (chunk_hash TEXT PRIMARY KEY, content TEXT)")
                
                # 6. Edges
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS edges (
                        source_id UUID, target_id UUID, relation_type TEXT, metadata JSONB
                    )
                """)
                cur.execute("CREATE INDEX IF NOT EXISTS idx_edges_src ON edges (source_id)")
                cur.execute("CREATE INDEX IF NOT EXISTS idx_edges_tgt ON edges (target_id)")

                # 7. FTS Unified Index
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS nodes_fts (
                        node_id UUID PRIMARY KEY REFERENCES nodes(id) ON DELETE CASCADE,
                        file_path TEXT, semantic_tags TEXT, content TEXT, search_vector TSVECTOR
                    )
                """)
                cur.execute("CREATE INDEX IF NOT EXISTS idx_fts_vec ON nodes_fts USING GIN (search_vector)")

                # 8. Embeddings (pgvector)
                cur.execute(f"""
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
                cur.execute("""
                    CREATE INDEX IF NOT EXISTS idx_emb_vector 
                    ON node_embeddings USING hnsw (embedding vector_cosine_ops)
                """)
                cur.execute("CREATE INDEX IF NOT EXISTS idx_emb_repo ON node_embeddings (repo_id)")

        # [FIX CRITICO] Reset del Pool
        # Chiudiamo il vecchio pool e ne creiamo uno NUOVO.
        # Questo farà scattare _configure_connection sulle nuove connessioni,
        # che ora troveranno l'estensione vector e caricheranno i tipi corretti.
        self.pool.close()
        self._create_pool()

    # ==========================================
    # FILTER HELPER
    # ==========================================
    
    def _build_filter_clause(self, filters: Dict[str, Any]) -> Tuple[str, List[Any]]:
        if not filters: return "", []
        clauses = []; params = []
        def as_list(val): return val if isinstance(val, list) else [val]

        # Path
        if filters.get("path_prefix"):
            paths = as_list(filters["path_prefix"])
            clauses.append("f.path LIKE ANY(%s)")
            params.append([p.strip('/') + '%' for p in paths])

        # Language
        if filters.get("language"):
            clauses.append("f.language = ANY(%s)"); params.append(as_list(filters["language"]))
        if filters.get("exclude_language"):
            clauses.append("f.language != ALL(%s)"); params.append(as_list(filters["exclude_language"]))

        # Semantic Filters
        def add_json_match(key, values, exclude=False):
            vals = as_list(values)
            json_ors = []
            for v in vals:
                # Cerca l'oggetto {"value": v} dentro l'array semantic_matches
                json_pattern = json.dumps({"semantic_matches": [{"value": v}]})
                json_ors.append(f"n.metadata @> %s::jsonb")
                params.append(json_pattern)
            combined = f"({' OR '.join(json_ors)})"
            if exclude: clauses.append(f"NOT {combined}")
            else: clauses.append(combined)

        if "role" in filters: add_json_match("value", filters["role"]) 
        if "exclude_role" in filters: add_json_match("value", filters["exclude_role"], exclude=True)

        # Category (Hybrid) - [FIX] Ordine parametri corretto
        if filters.get("category"):
            cats = as_list(filters["category"])
            
            # Parametri per chunk logic (JSONB)
            chunk_params = []
            json_ors = []
            for c in cats:
                json_ors.append(f"n.metadata @> %s::jsonb")
                chunk_params.append(json.dumps({"semantic_matches": [{"category": c}]}))
            
            file_logic = "f.category = ANY(%s)"
            chunk_logic = " OR ".join(json_ors)
            
            clauses.append(f"({file_logic} OR {chunk_logic})")
            
            # ORDINE PARAMETRI: Prima File, Poi Chunk
            params.append(cats)
            params.extend(chunk_params)

        if filters.get("exclude_category"):
            ex_cats = as_list(filters["exclude_category"])
            
            chunk_params = []
            json_ors = []
            for c in ex_cats:
                json_ors.append(f"n.metadata @> %s::jsonb")
                chunk_params.append(json.dumps({"semantic_matches": [{"category": c}]}))
            
            file_logic = "f.category != ALL(%s)"
            chunk_logic = f"NOT ({' OR '.join(json_ors)})"
            
            clauses.append(f"({file_logic} AND {chunk_logic})")
            
            # ORDINE PARAMETRI: Prima File, Poi Chunk
            params.append(ex_cats)
            params.extend(chunk_params)

        if not clauses: return "", []
        return " AND " + " AND ".join(clauses), params

    # ==========================================
    # WRITE METHODS
    # ==========================================

    def add_files(self, files: List[Any]):
        if not files: return
        with self.pool.connection() as conn:
            with conn.cursor() as cur:
                data = [f.to_dict() for f in files]
                cur.executemany("""
                    INSERT INTO files (id, repo_id, commit_hash, file_hash, path, language, size_bytes, category, indexed_at)
                    VALUES (%(id)s, %(repo_id)s, %(commit_hash)s, %(file_hash)s, %(path)s, %(language)s, %(size_bytes)s, %(category)s, %(indexed_at)s)
                    ON CONFLICT (repo_id, path) DO UPDATE 
                    SET commit_hash=EXCLUDED.commit_hash, file_hash=EXCLUDED.file_hash, size_bytes=EXCLUDED.size_bytes, indexed_at=EXCLUDED.indexed_at
                """, data)

    def add_nodes(self, nodes: List[Any]):
        if not nodes: return
        with self.pool.connection() as conn:
            with conn.cursor() as cur:
                data = []
                for n in nodes:
                    d = n.to_dict()
                    d['metadata'] = json.dumps(d.get('metadata', {})) # JSONB richiede stringa o dict (psycopg3 gestisce entrambi, ma dumps è sicuro)
                    # Calcolo size mancante nel modello
                    b_start = d['byte_range'][0]
                    b_end = d['byte_range'][1]
                    d['byte_start'] = b_start
                    d['byte_end'] = b_end
                    d['size'] = b_end - b_start
                    data.append(d)
                
                cur.executemany("""
                    INSERT INTO nodes (
                        id, file_id, file_path, start_line, end_line, 
                        byte_start, byte_end, chunk_hash, size, metadata
                    )
                    VALUES (
                        %(id)s, %(file_id)s, %(file_path)s, 
                        %(start_line)s, %(end_line)s, 
                        %(byte_start)s, %(byte_end)s, 
                        %(chunk_hash)s, %(size)s, %(metadata)s
                    )
                    ON CONFLICT (id) DO NOTHING
                """, data)

    def add_contents(self, contents: List[Any]):
        if not contents: return
        with self.pool.connection() as conn:
            with conn.cursor() as cur:
                data = [c.to_dict() for c in contents]
                cur.executemany("""
                    INSERT INTO contents (chunk_hash, content) 
                    VALUES (%(chunk_hash)s, %(content)s)
                    ON CONFLICT (chunk_hash) DO NOTHING
                """, data)

    def add_edge(self, source_id: str, target_id: str, relation_type: str, metadata: Dict[str, Any]):
        with self.pool.connection() as conn:
            conn.execute(
                "INSERT INTO edges (source_id, target_id, relation_type, metadata) VALUES (%s, %s, %s, %s)",
                (source_id, target_id, relation_type, json.dumps(metadata))
            )

    def save_embeddings(self, vector_documents: List[Dict[str, Any]]):
        if not vector_documents: return
        with self.pool.connection() as conn:
            with conn.cursor() as cur:
                # [FIX] Usiamo %(embedding)s per matchare la chiave nel dict
                cur.executemany("""
                    INSERT INTO node_embeddings (
                        id, chunk_id, repo_id, file_path, branch, language, category,
                        start_line, end_line, vector_hash, model_name, created_at, embedding
                    ) VALUES (
                        %(id)s, %(chunk_id)s, %(repo_id)s, %(file_path)s, %(branch)s, %(language)s, %(category)s,
                        %(start_line)s, %(end_line)s, %(vector_hash)s, %(model_name)s, %(created_at)s, %(embedding)s
                    )
                    ON CONFLICT (id) DO NOTHING
                """, vector_documents)

    def add_search_index(self, search_docs: List[Dict[str, Any]]):
        if not search_docs: return
        with self.pool.connection() as conn:
            with conn.cursor() as cur:
                cur.executemany("""
                    INSERT INTO nodes_fts (node_id, file_path, semantic_tags, content, search_vector)
                    VALUES (
                        %(node_id)s, %(file_path)s, %(tags)s, %(content)s,
                        setweight(to_tsvector('english', %(tags)s), 'A') || 
                        setweight(to_tsvector('english', %(content)s), 'B')
                    )
                    ON CONFLICT (node_id) DO UPDATE 
                    SET search_vector = EXCLUDED.search_vector,
                        content = EXCLUDED.content,
                        semantic_tags = EXCLUDED.semantic_tags
                """, search_docs)

    # ==========================================
    # RETRIEVAL & LOOKUP
    # ==========================================

    def search_vectors(self, query_vector: List[float], limit: int = 20, 
                       repo_id: str = None, branch: str = None, 
                       filters: Dict[str, Any] = None) -> List[Dict[str, Any]]:
        
        sql = """
            SELECT ne.id, ne.file_path, ne.start_line, ne.end_line, 
                   ne.repo_id, ne.branch, n.metadata, c.content,
                   (ne.embedding <=> %s::vector) as distance
            FROM node_embeddings ne
            JOIN nodes n ON ne.chunk_id = n.id
            JOIN contents c ON n.chunk_hash = c.chunk_hash
            JOIN files f ON n.file_id = f.id
            WHERE 1=1
        """
        params = [query_vector]
        
        if repo_id:
            sql += " AND ne.repo_id = %s"; params.append(repo_id)
        if branch:
            sql += " AND ne.branch = %s"; params.append(branch)
            
        filter_sql, filter_params = self._build_filter_clause(filters)
        sql += filter_sql
        params.extend(filter_params)
        
        sql += " ORDER BY distance ASC LIMIT %s"
        params.append(limit)

        with self.pool.connection() as conn:
            results = []
            for row in conn.execute(sql, params).fetchall():
                sim = 1 - row['distance']
                results.append({
                    "id": str(row['id']),
                    "file_path": row['file_path'],
                    "start_line": row['start_line'],
                    "end_line": row['end_line'],
                    "repo_id": str(row['repo_id']),
                    "branch": row['branch'],
                    "metadata": row['metadata'],
                    "content": row['content'],
                    "score": sim
                })
            return results

    def search_fts(self, query: str, limit: int = 20, repo_id: str = None, 
                   branch: str = None, filters: Dict[str, Any] = None) -> List[Dict[str, Any]]:
        
        sql = """
            SELECT 
                fts.node_id, fts.file_path, n.start_line, n.end_line, 
                fts.content, f.repo_id, r.branch, n.metadata,
                ts_rank(fts.search_vector, websearch_to_tsquery('english', %s)) as rank
            FROM nodes_fts fts
            JOIN nodes n ON fts.node_id = n.id
            JOIN files f ON n.file_id = f.id
            JOIN repositories r ON f.repo_id = r.id
            WHERE fts.search_vector @@ websearch_to_tsquery('english', %s)
        """
        params = [query, query] 
        
        if repo_id:
            sql += " AND f.repo_id = %s"; params.append(repo_id)
        if branch:
            sql += " AND r.branch = %s"; params.append(branch)
            
        filter_sql, filter_params = self._build_filter_clause(filters)
        sql += filter_sql
        params.extend(filter_params)
        
        sql += " ORDER BY rank DESC LIMIT %s"
        params.append(limit)

        try:
            with self.pool.connection() as conn:
                results = []
                for row in conn.execute(sql, params).fetchall():
                    results.append({
                        "id": str(row['node_id']),
                        "file_path": row['file_path'],
                        "start_line": row['start_line'],
                        "end_line": row['end_line'],
                        "score": row['rank'],
                        "content": row['content'],
                        "repo_id": str(row['repo_id']),
                        "branch": row['branch'],
                        "metadata": row['metadata']
                    })
                return results
        except Exception as e:
            logger.error(f"Postgres FTS Error: {e}")
            return []

    # --- CACHE & UTILS ---

    def get_vectors_by_hashes(self, vector_hashes: List[str], model_name: str) -> Dict[str, List[float]]:
        # Metodo stub se non implementiamo ancora la cache globale
        # In Postgres, potremmo fare una query su unique_vectors se esistesse
        return {} 

    def get_nodes_to_embed(self, repo_id: str, model_name: str) -> Generator[Dict[str, Any], None, None]:
        sql = """
            SELECT n.id, n.file_path, n.chunk_hash, n.start_line, n.end_line, n.metadata,
                   f.repo_id, r.branch, f.language, f.category 
            FROM files f
            JOIN repositories r ON f.repo_id = r.id
            JOIN nodes n ON f.id = n.file_id
            LEFT JOIN node_embeddings ne ON (n.id = ne.chunk_id AND ne.model_name = %s)
            WHERE f.repo_id = %s AND ne.id IS NULL
        """
        with self.pool.connection() as conn:
            with conn.transaction(): # Transazione esplicita per evitare NoActiveSqlTransaction
                rows = conn.execute(sql, (model_name, repo_id)).fetchall()
            
            for r in rows:
                yield {
                    "id": str(r['id']), 
                    "file_path": r['file_path'], 
                    "chunk_hash": r['chunk_hash'], 
                    "start_line": r['start_line'],
                    "end_line": r['end_line'], 
                    "metadata_json": json.dumps(r['metadata']), 
                    "repo_id": str(r['repo_id']), 
                    "branch": r['branch'],
                    "language": r['language'], 
                    "category": r['category']
                }

    def find_chunk_id(self, file_path: str, byte_range: List[int], repo_id: str = None) -> Optional[str]:
        if not byte_range: return None
        sql = "SELECT n.id FROM nodes n JOIN files f ON n.file_id = f.id WHERE f.path = %s AND n.byte_start <= %s + 1 AND n.byte_end >= %s - 1"
        params = [file_path, byte_range[0], byte_range[1]]
        if repo_id: sql += " AND f.repo_id = %s"; params.append(repo_id)
        sql += " ORDER BY n.size ASC LIMIT 1"
        with self.pool.connection() as conn:
            row = conn.execute(sql, params).fetchone()
            return str(row['id']) if row else None

    def get_stats(self):
        with self.pool.connection() as conn:
            return {
                "files": conn.execute("SELECT COUNT(*) as c FROM files").fetchone()['c'],
                "total_nodes": conn.execute("SELECT COUNT(*) as c FROM nodes").fetchone()['c'],
                "embeddings": conn.execute("SELECT COUNT(*) as c FROM node_embeddings").fetchone()['c'],
                "repositories": conn.execute("SELECT COUNT(*) as c FROM repositories").fetchone()['c']
            }

    # ... Metodi standard (copia 1:1 dalla versione precedente o sqlite.py) ...
    def get_repository(self, repo_id):
        with self.pool.connection() as conn:
            return conn.execute("SELECT * FROM repositories WHERE id=%s", (repo_id,)).fetchone()
            
    def get_repository_by_context(self, url, branch):
        with self.pool.connection() as conn:
            return conn.execute("SELECT * FROM repositories WHERE url=%s AND branch=%s", (url, branch)).fetchone()

    def register_repository(self, id, name, url, branch, commit_hash, local_path=None):
        now = datetime.datetime.utcnow()
        with self.pool.connection() as conn:
            repo = self.get_repository_by_context(url, branch)
            if repo:
                rid = repo['id']
                conn.execute("UPDATE repositories SET name=%s, last_commit=%s, status='indexing', updated_at=%s, local_path=%s WHERE id=%s", 
                             (name, commit_hash, now, local_path, rid))
            else:
                rid = str(uuid.uuid4())
                conn.execute("INSERT INTO repositories (id, name, url, branch, last_commit, status, updated_at, local_path) VALUES (%s, %s, %s, %s, %s, 'indexing', %s, %s)",
                             (rid, name, url, branch, commit_hash, now, local_path))
            return str(rid)

    def update_repository_status(self, repo_id, status, commit_hash=None):
        now = datetime.datetime.utcnow()
        sql = "UPDATE repositories SET status=%s, updated_at=%s" + (", last_commit=%s" if commit_hash else "") + " WHERE id=%s"
        params = [status, now, commit_hash, repo_id] if commit_hash else [status, now, repo_id]
        with self.pool.connection() as conn:
            conn.execute(sql, params)

    def delete_previous_data(self, repo_id, branch):
        try:
            with self.pool.connection() as conn:
                conn.execute("DELETE FROM node_embeddings WHERE repo_id=%s", (repo_id,))
                conn.execute("DELETE FROM edges WHERE source_id IN (SELECT n.id FROM nodes n JOIN files f ON n.file_id=f.id WHERE f.repo_id=%s)", (repo_id,))
                conn.execute("DELETE FROM nodes WHERE file_id IN (SELECT id FROM files WHERE repo_id=%s)", (repo_id,))
                conn.execute("DELETE FROM files WHERE repo_id=%s", (repo_id,))
        except Exception as e: logger.error(f"Del error: {e}")

    # Navigator methods
    def get_context_neighbors(self, node_id):
        res = {"parents": [], "calls": []}
        with self.pool.connection() as conn:
            for r in conn.execute("SELECT t.id, t.file_path, t.start_line, e.metadata, t.metadata FROM edges e JOIN nodes t ON e.target_id=t.id WHERE e.source_id=%s AND e.relation_type='child_of'", (node_id,)).fetchall():
                res["parents"].append({"id": str(r['id']), "file_path": r['file_path'], "start_line": r['start_line'], "edge_meta": r['metadata'], "metadata": r['metadata']})
            for r in conn.execute("SELECT t.id, t.file_path, e.metadata FROM edges e JOIN nodes t ON e.target_id=t.id WHERE e.source_id=%s AND e.relation_type IN ('calls','references') LIMIT 15", (node_id,)).fetchall():
                res["calls"].append({"id": str(r['id']), "symbol": r['metadata'].get("symbol", "unknown")})
        return res

    def get_neighbor_chunk(self, node_id, direction="next"):
        with self.pool.connection() as conn:
            curr = conn.execute("SELECT file_id, start_line, end_line FROM nodes WHERE id=%s", (node_id,)).fetchone()
            if not curr: return None
            fid, s, e = curr['file_id'], curr['start_line'], curr['end_line']
            
            if direction == "next":
                sql = "SELECT n.id, n.start_line, n.end_line, n.chunk_hash, c.content, n.metadata FROM nodes n JOIN contents c ON n.chunk_hash=c.chunk_hash WHERE n.file_id=%s AND n.start_line >= %s AND n.id!=%s ORDER BY n.start_line ASC LIMIT 1"
                p = (fid, e, node_id)
            else:
                sql = "SELECT n.id, n.start_line, n.end_line, n.chunk_hash, c.content, n.metadata FROM nodes n JOIN contents c ON n.chunk_hash=c.chunk_hash WHERE n.file_id=%s AND n.end_line <= %s AND n.id!=%s ORDER BY n.end_line DESC LIMIT 1"
                p = (fid, s, node_id)
            
            row = conn.execute(sql, p).fetchone()
            if row: return {"id": str(row['id']), "start_line": row['start_line'], "end_line": row['end_line'], "chunk_hash": row['chunk_hash'], "content": row['content'], "metadata": row['metadata']}
            return None

    def get_incoming_references(self, target_node_id, limit=50):
        with self.pool.connection() as conn:
            res = []
            for r in conn.execute("SELECT s.id, s.file_path, s.start_line, e.relation_type, e.metadata FROM edges e JOIN nodes s ON e.source_id=s.id WHERE e.target_id=%s AND e.relation_type IN ('calls', 'references', 'imports', 'instantiates') ORDER BY s.file_path, s.start_line LIMIT %s", (target_node_id, limit)).fetchall():
                res.append({"source_id": str(r['id']), "file": r['file_path'], "line": r['start_line'], "relation": r['relation_type'], "context_snippet": r['metadata'].get("description", "")})
            return res

    def get_outgoing_calls(self, source_node_id, limit=50):
        with self.pool.connection() as conn:
            res = []
            for r in conn.execute("SELECT t.id, t.file_path, t.start_line, e.relation_type, e.metadata FROM edges e JOIN nodes t ON e.target_id=t.id WHERE e.source_id=%s AND e.relation_type IN ('calls', 'instantiates', 'imports') ORDER BY t.file_path, t.start_line LIMIT %s", (source_node_id, limit)).fetchall():
                res.append({"target_id": str(r['id']), "file": r['file_path'], "line": r['start_line'], "relation": r['relation_type'], "symbol": r['metadata'].get("symbol", "")})
            return res

    def get_files_bulk(self, file_paths: List[str], repo_id: str = None) -> Dict[str, Dict[str, Any]]:
        if not file_paths: return {}
        unique = list(set(file_paths))
        res = {}
        with self.pool.connection() as conn:
            for i in range(0, len(unique), 500):
                batch = unique[i:i+500]
                sql = "SELECT path, repo_id, language, category FROM files WHERE path = ANY(%s)"
                params = [batch]
                if repo_id: sql += " AND repo_id=%s"; params.append(repo_id)
                for r in conn.execute(sql, params).fetchall():
                    res[r['path']] = dict(r)
        return res

    def get_contents_bulk(self, chunk_hashes: List[str]) -> Dict[str, str]:
        if not chunk_hashes: return {}
        res = {}
        with self.pool.connection() as conn:
            for i in range(0, len(chunk_hashes), 500):
                batch = chunk_hashes[i:i+500]
                for r in conn.execute("SELECT chunk_hash, content FROM contents WHERE chunk_hash = ANY(%s)", (batch,)).fetchall():
                    res[r['chunk_hash']] = r['content']
        return res

    def get_incoming_definitions_bulk(self, node_ids: List[str]) -> Dict[str, List[str]]:
        if not node_ids: return {}
        res = {}
        with self.pool.connection() as conn:
            for i in range(0, len(node_ids), 500):
                batch = node_ids[i:i+500]
                for r in conn.execute("SELECT target_id, metadata FROM edges WHERE target_id = ANY(%s) AND relation_type='calls'", (batch,)).fetchall():
                    sym = r['metadata'].get("symbol")
                    if sym:
                        tid = str(r['target_id'])
                        if tid not in res: res[tid] = set()
                        res[tid].add(sym)
        return {k: list(v) for k, v in res.items()}

    def get_nodes_cursor(self, **kwargs): yield from []
    def ensure_external_node(self, nid): pass
    def get_all_files(self): yield from []
    def get_all_nodes(self): yield from []
    def get_all_contents(self): yield from []
    def get_all_edges(self): yield from []