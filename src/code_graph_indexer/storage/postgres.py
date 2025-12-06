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
        self._db_url = db_url
        self.vector_dim = vector_dim
        
        self._min_size = min_size
        self._max_size = max_size
        self._pool_kwargs = {
            "row_factory": dict_row,
            "autocommit": True 
        }
        
        safe_url = db_url.split('@')[-1] if '@' in db_url else "..."
        logger.info(f"🐘 Connecting to Postgres (Pool): {safe_url} | Vector Dim: {vector_dim}")
        
        self._create_pool()
        self._init_schema()

    def _create_pool(self):
        self.pool = ConnectionPool(
            conninfo=self._db_url,
            min_size=self._min_size,
            max_size=self._max_size,
            kwargs=self._pool_kwargs,
            configure=self._configure_connection
        )
        self.pool.wait()

    def _configure_connection(self, conn: psycopg.Connection):
        try:
            register_vector(conn)
        except psycopg.ProgrammingError:
            pass 

    def close(self):
        if hasattr(self, 'pool') and self.pool:
            self.pool.close()
            logger.info("🐘 Postgres Pool closed.")

    def commit(self):
        pass

    def _init_schema(self):
        with self.pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute("CREATE EXTENSION IF NOT EXISTS vector")
                cur.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
                
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS repositories (
                        id UUID PRIMARY KEY,
                        url TEXT NOT NULL, branch TEXT NOT NULL,
                        name TEXT, last_commit TEXT, status TEXT, updated_at TIMESTAMP, local_path TEXT,
                        queued_commit TEXT,
                        UNIQUE(url, branch)
                    )
                """)

                cur.execute("""
                    CREATE TABLE IF NOT EXISTS files (
                        id UUID PRIMARY KEY,
                        repo_id UUID REFERENCES repositories(id),
                        commit_hash TEXT, file_hash TEXT,
                        path TEXT, language TEXT, size_bytes BIGINT, category TEXT, indexed_at TIMESTAMP,
                        parsing_status TEXT DEFAULT 'success', parsing_error TEXT,
                        UNIQUE(repo_id, path)
                    )
                """)
                cur.execute("CREATE INDEX IF NOT EXISTS idx_files_repo ON files (repo_id)")
                cur.execute("CREATE INDEX IF NOT EXISTS idx_files_path ON files (path)")
                cur.execute("CREATE INDEX IF NOT EXISTS idx_files_status ON files (parsing_status)")

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

                cur.execute("CREATE TABLE IF NOT EXISTS contents (chunk_hash TEXT PRIMARY KEY, content TEXT)")
                
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS edges (
                        source_id UUID, target_id UUID, relation_type TEXT, metadata JSONB
                    )
                """)
                cur.execute("CREATE INDEX IF NOT EXISTS idx_edges_src ON edges (source_id)")
                cur.execute("CREATE INDEX IF NOT EXISTS idx_edges_tgt ON edges (target_id)")

                cur.execute("""
                    CREATE TABLE IF NOT EXISTS nodes_fts (
                        node_id UUID PRIMARY KEY REFERENCES nodes(id) ON DELETE CASCADE,
                        file_path TEXT, semantic_tags TEXT, content TEXT, search_vector TSVECTOR
                    )
                """)
                cur.execute("CREATE INDEX IF NOT EXISTS idx_fts_vec ON nodes_fts USING GIN (search_vector)")

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

        self.pool.close()
        self._create_pool()

    def acquire_indexing_lock(self, url: str, branch: str, name: str, 
                            commit_hash: str, local_path: str = None, 
                            timeout_minutes: int = 30) -> Tuple[bool, Optional[str]]:
        
        now = datetime.datetime.utcnow()
        threshold = now - datetime.timedelta(minutes=timeout_minutes)
        existing = self.get_repository_by_context(url, branch)
        
        with self.pool.connection() as conn:
            if existing:
                repo_id = str(existing['id'])
                current_status = existing['status']
                last_update = existing['updated_at']
                
                if current_status != 'indexing' or (last_update and last_update < threshold):
                    conn.execute("""
                        UPDATE repositories 
                        SET status='indexing', updated_at=%s, name=%s, last_commit=%s, local_path=%s, queued_commit=NULL
                        WHERE id=%s
                    """, (now, name, commit_hash, local_path, repo_id))
                    return True, repo_id
                else:
                    logger.info(f"⏳ Repo occupato. Accodo commit {commit_hash[:8]} per dopo.")
                    conn.execute("UPDATE repositories SET queued_commit = %s WHERE id = %s", (commit_hash, repo_id))
                    return False, repo_id
            else:
                new_id = str(uuid.uuid4())
                try:
                    conn.execute("""
                        INSERT INTO repositories (id, url, branch, name, last_commit, status, updated_at, local_path)
                        VALUES (%s, %s, %s, %s, %s, 'indexing', %s, %s)
                    """, (new_id, url, branch, name, commit_hash, now, local_path))
                    return True, new_id
                except psycopg.errors.UniqueViolation:
                    return False, None

    def release_indexing_lock(self, repo_id: str, success: bool, commit_hash: str = None) -> Optional[str]:
        now = datetime.datetime.utcnow()
        with self.pool.connection() as conn:
            if not success:
                conn.execute("UPDATE repositories SET status='failed', updated_at=%s WHERE id=%s", (now, repo_id))
                logger.info(f"🔓 Lock RILASCIATO per {repo_id} (Status: failed)")
                return None

            with conn.transaction():
                row = conn.execute("SELECT queued_commit FROM repositories WHERE id=%s FOR UPDATE", (repo_id,)).fetchone()
                next_commit = row['queued_commit'] if row else None
                
                if next_commit:
                    logger.info(f"🔄 Trovato lavoro in coda ({next_commit[:8]}). Il worker continua.")
                    conn.execute("UPDATE repositories SET last_commit=%s, updated_at=%s, queued_commit=NULL WHERE id=%s", (commit_hash, now, repo_id))
                    return next_commit
                else:
                    conn.execute("UPDATE repositories SET status='completed', last_commit=%s, updated_at=%s WHERE id=%s", (commit_hash, now, repo_id))
                    logger.info(f"🔓 Coda vuota. Lock RILASCIATO per {repo_id} (Status: completed)")
                    return None

    # ==========================================
    # FILTER HELPER (FIXED)
    # ==========================================
    
    def _build_filter_clause(self, filters: Dict[str, Any]) -> Tuple[str, List[Any]]:
        if not filters: return "", []
        
        clauses = []
        params = []
        
        def as_list(val): return val if isinstance(val, list) else [val]

        # 1. Path
        # Usa get() così se è None o [] (falsy) viene saltato
        if filters.get("path_prefix"):
            paths = as_list(filters["path_prefix"])
            if paths:
                clauses.append("f.path LIKE ANY(%s)")
                params.append([p.strip('/') + '%' for p in paths])

        # 2. Language
        if filters.get("language"):
            langs = as_list(filters["language"])
            if langs:
                clauses.append("f.language = ANY(%s)")
                params.append(langs)
            
        if filters.get("exclude_language"):
            ex_langs = as_list(filters["exclude_language"])
            if ex_langs:
                clauses.append("f.language != ALL(%s)")
                params.append(ex_langs)

        # 3. Semantic Filters (JSON)
        def add_json_match(key, values, exclude=False):
            vals = as_list(values)
            if not vals: return # [FIX] Se la lista è vuota, esci subito!
            
            json_ors = []
            for v in vals:
                # Cerca l'oggetto {"value": v} dentro l'array semantic_matches
                json_pattern = json.dumps({"semantic_matches": [{"value": v}]})
                json_ors.append(f"n.metadata @> %s::jsonb")
                params.append(json_pattern)
            
            combined = f"({' OR '.join(json_ors)})"
            if exclude: clauses.append(f"NOT {combined}")
            else: clauses.append(combined)

        # [FIX] Usiamo .get() o controlliamo che il valore non sia vuoto
        if filters.get("role"): 
            add_json_match("value", filters["role"]) 
            
        if filters.get("exclude_role"): 
            add_json_match("value", filters["exclude_role"], exclude=True)

        # 4. Category (Hybrid)
        if filters.get("category"):
            cats = as_list(filters["category"])
            if cats:
                json_ors = []
                for c in cats:
                    json_ors.append(f"n.metadata @> %s::jsonb")
                    params.append(json.dumps({"semantic_matches": [{"category": c}]}))
                
                chunk_logic = " OR ".join(json_ors)
                file_logic = "f.category = ANY(%s)"
                
                clauses.append(f"({file_logic} OR {chunk_logic})")
                
                # Ordine Parametri: File (ANY) -> Chunk (JSONB)
                params.append(cats)
                # Non aggiungiamo params per chunk_logic qui perché l'abbiamo fatto nel loop
                # Ops, nel loop sopra ho fatto params.append!
                # ATTENZIONE ALL'ORDINE: 
                # Il loop `for c in cats` ha già appeso i params per il chunk.
                # MA la stringa SQL mette `file_logic` PRIMA di `chunk_logic`.
                # Quindi i parametri `cats` (per file_logic) devono essere inseriti PRIMA di quelli JSON.
                
                # CORREZIONE LOGICA ACCUMULO PARAMETRI:
                # Dobbiamo rimuovere i params aggiunti nel loop e rimetterli nell'ordine giusto,
                # oppure usare liste temporanee.
                
                # Resettiamo l'errore logico fatto sopra:
                # Riscriviamo il blocco category per essere sicuri dell'ordine.

        # --- BLOCCO CATEGORY RISCRITTO E SICURO ---
        
        # Category Include
        if filters.get("category"):
            cats = as_list(filters["category"])
            if cats:
                # Parametri per la parte JSON
                json_params_temp = []
                json_ors = []
                
                for c in cats:
                    json_ors.append(f"n.metadata @> %s::jsonb")
                    json_params_temp.append(json.dumps({"semantic_matches": [{"category": c}]}))
                
                chunk_logic = " OR ".join(json_ors)
                file_logic = "f.category = ANY(%s)"
                
                clauses.append(f"({file_logic} OR {chunk_logic})")
                
                # Ordine SQL: file_logic (cats) -> chunk_logic (json_params)
                params.append(cats) 
                params.extend(json_params_temp)

        # Category Exclude
        if filters.get("exclude_category"):
            ex_cats = as_list(filters["exclude_category"])
            if ex_cats:
                json_params_temp = []
                json_ors = []
                
                for c in ex_cats:
                    json_ors.append(f"n.metadata @> %s::jsonb")
                    json_params_temp.append(json.dumps({"semantic_matches": [{"category": c}]}))
                
                chunk_logic = f"NOT ({' OR '.join(json_ors)})"
                file_logic = "f.category != ALL(%s)"
                
                clauses.append(f"({file_logic} AND {chunk_logic})")
                
                # Ordine SQL: file_logic (ex_cats) -> chunk_logic (json_params)
                params.append(ex_cats)
                params.extend(json_params_temp)

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
                    INSERT INTO files (id, repo_id, commit_hash, file_hash, path, language, size_bytes, category, indexed_at, parsing_status, parsing_error)
                    VALUES (%(id)s, %(repo_id)s, %(commit_hash)s, %(file_hash)s, %(path)s, %(language)s, %(size_bytes)s, %(category)s, %(indexed_at)s, %(parsing_status)s, %(parsing_error)s)
                    ON CONFLICT (repo_id, path) DO UPDATE 
                    SET commit_hash=EXCLUDED.commit_hash, file_hash=EXCLUDED.file_hash, size_bytes=EXCLUDED.size_bytes, indexed_at=EXCLUDED.indexed_at, parsing_status=EXCLUDED.parsing_status, parsing_error=EXCLUDED.parsing_error
                """, data)

    def add_nodes(self, nodes: List[Any]):
        if not nodes: return
        with self.pool.connection() as conn:
            with conn.cursor() as cur:
                data = []
                for n in nodes:
                    d = n.to_dict()
                    d['metadata'] = json.dumps(d.get('metadata', {}))
                    d['byte_start'] = d['byte_range'][0]
                    d['byte_end'] = d['byte_range'][1]
                    d['size'] = d['byte_end'] - d['byte_start']
                    data.append(d)
                
                cur.executemany("""
                    INSERT INTO nodes (id, file_id, file_path, start_line, end_line, byte_start, byte_end, chunk_hash, size, metadata)
                    VALUES (%(id)s, %(file_id)s, %(file_path)s, %(start_line)s, %(end_line)s, %(byte_start)s, %(byte_end)s, %(chunk_hash)s, %(size)s, %(metadata)s)
                    ON CONFLICT (id) DO NOTHING
                """, data)

    def add_contents(self, contents: List[Any]):
        if not contents: return
        with self.pool.connection() as conn:
            with conn.cursor() as cur:
                data = [c.to_dict() for c in contents]
                cur.executemany("INSERT INTO contents (chunk_hash, content) VALUES (%(chunk_hash)s, %(content)s) ON CONFLICT (chunk_hash) DO NOTHING", data)

    def add_edge(self, source_id, target_id, relation_type, metadata):
        with self.pool.connection() as conn:
            conn.execute("INSERT INTO edges (source_id, target_id, relation_type, metadata) VALUES (%s, %s, %s, %s)", (source_id, target_id, relation_type, json.dumps(metadata)))

    def save_embeddings(self, vector_documents: List[Dict[str, Any]]):
        if not vector_documents: return
        with self.pool.connection() as conn:
            with conn.cursor() as cur:
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
                    SET search_vector = EXCLUDED.search_vector, content = EXCLUDED.content, semantic_tags = EXCLUDED.semantic_tags
                """, search_docs)

    # ==========================================
    # RETRIEVAL
    # ==========================================

    def search_vectors(self, query_vector: List[float], limit: int = 20, 
                       repo_id: str = None, branch: str = None, 
                       filters: Dict[str, Any] = None) -> List[Dict[str, Any]]:
        
        sql = """
            SELECT ne.chunk_id, ne.file_path, ne.start_line, ne.end_line, 
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
                    "id": str(row['chunk_id']),
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

    # --- BATCH & UTILS ---
    def get_nodes_to_embed(self, repo_id: str, model_name: str, batch_size: int = 2000) -> Generator[Dict[str, Any], None, None]:
        """
        Recupera i nodi da embeddare usando un Server-Side Cursor per efficienza RAM.
        """
        # Query invariata: recupera tutto il contesto necessario per l'embedding
        sql = """
            SELECT n.id, n.file_path, n.chunk_hash, n.start_line, n.end_line, n.metadata,
                   f.repo_id, r.branch, f.language, f.category 
            FROM files f
            JOIN repositories r ON f.repo_id = r.id
            JOIN nodes n ON f.id = n.file_id
            LEFT JOIN node_embeddings ne ON (n.id = ne.chunk_id AND ne.model_name = %s)
            WHERE f.repo_id = %s AND ne.id IS NULL
        """
        
        import uuid
        # Generiamo un nome univoco per il cursore lato server
        cursor_name = f"embed_stream_{uuid.uuid4().hex}"

        # [FIX] Otteniamo una connessione dal pool (context manager gestisce il rilascio)
        with self.pool.connection() as conn:
            
            # I cursori lato server in Postgres richiedono una transazione attiva
            with conn.transaction():
                
                # 'name' attiva la modalità server-side. 'row_factory=dict_row' è già settato nel pool.
                with conn.cursor(name=cursor_name) as cur:
                    cur.itersize = batch_size
                    
                    # Esegue la query (ma non scarica i dati ancora)
                    cur.execute(sql, (model_name, repo_id))
                    
                    # Itera sui risultati chunk per chunk
                    for r in cur:
                        yield {
                            "id": str(r['id']), 
                            "file_path": r['file_path'], 
                            "chunk_hash": r['chunk_hash'], 
                            "start_line": r['start_line'],
                            "end_line": r['end_line'], 
                            # Postgres restituisce già un dict per le colonne JSONB, lo riconvertiamo a stringa per coerenza interna
                            "metadata_json": json.dumps(r['metadata']),
                            "repo_id": str(r['repo_id']), 
                            "branch": r['branch'],
                            "language": r['language'], 
                            "category": r['category']
                        }
    def get_vectors_by_hashes(self, vector_hashes: List[str], model_name: str) -> Dict[str, List[float]]:
        if not vector_hashes: return {}
        res = {}
        with self.pool.connection() as conn:
            query = "SELECT DISTINCT ON (vector_hash) vector_hash, embedding FROM node_embeddings WHERE vector_hash = ANY(%s) AND model_name = %s"
            for r in conn.execute(query, (vector_hashes, model_name)).fetchall():
                if r['embedding'] is not None: res[r['vector_hash']] = r['embedding']
        return res

    def find_chunk_id(self, file_path, byte_range, repo_id=None):
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
                "total_edges": conn.execute("SELECT COUNT(*) as c FROM edges").fetchone()['c'],
                "embeddings": conn.execute("SELECT COUNT(*) as c FROM node_embeddings").fetchone()['c'],
                "repositories": conn.execute("SELECT COUNT(*) as c FROM repositories").fetchone()['c']
            }

    def get_repository(self, repo_id):
        with self.pool.connection() as conn: return conn.execute("SELECT * FROM repositories WHERE id=%s", (repo_id,)).fetchone()
            
    def get_repository_by_context(self, url, branch):
        with self.pool.connection() as conn: return conn.execute("SELECT * FROM repositories WHERE url=%s AND branch=%s", (url, branch)).fetchone()

    def register_repository(self, *args, **kwargs): pass # Legacy

    def update_repository_status(self, repo_id, status, commit_hash=None):
        now = datetime.datetime.utcnow()
        sql = "UPDATE repositories SET status=%s, updated_at=%s" + (", last_commit=%s" if commit_hash else "") + " WHERE id=%s"
        params = [status, now, commit_hash, repo_id] if commit_hash else [status, now, repo_id]
        with self.pool.connection() as conn: conn.execute(sql, params)

    def delete_previous_data(self, repo_id, branch):
        try:
            with self.pool.connection() as conn:
                conn.execute("DELETE FROM node_embeddings WHERE repo_id=%s", (repo_id,))
                conn.execute("DELETE FROM edges WHERE source_id IN (SELECT n.id FROM nodes n JOIN files f ON n.file_id=f.id WHERE f.repo_id=%s)", (repo_id,))
                conn.execute("DELETE FROM nodes WHERE file_id IN (SELECT id FROM files WHERE repo_id=%s)", (repo_id,))
                conn.execute("DELETE FROM files WHERE repo_id=%s", (repo_id,))
        except Exception as e: logger.error(f"Del error: {e}")

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
                sql = "SELECT n.id, n.start_line, n.end_line, n.chunk_hash, c.content, n.metadata, n.file_path FROM nodes n JOIN contents c ON n.chunk_hash=c.chunk_hash WHERE n.file_id=%s AND n.start_line >= %s AND n.id!=%s ORDER BY n.start_line ASC LIMIT 1"
                p = (fid, e, node_id)
            else:
                sql = "SELECT n.id, n.start_line, n.end_line, n.chunk_hash, c.content, n.metadata, n.file_path FROM nodes n JOIN contents c ON n.chunk_hash=c.chunk_hash WHERE n.file_id=%s AND n.end_line <= %s AND n.id!=%s ORDER BY n.end_line DESC LIMIT 1"
                p = (fid, s, node_id)
            row = conn.execute(sql, p).fetchone()
            if row: return {"id": str(row['id']), "start_line": row['start_line'], "end_line": row['end_line'], "chunk_hash": row['chunk_hash'], "content": row['content'], "metadata": row['metadata'], "file_path": row['file_path']}
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
                for r in conn.execute(sql, params).fetchall(): res[r['path']] = dict(r)
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