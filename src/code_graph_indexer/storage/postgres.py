import json
import logging
import uuid
from typing import List, Dict, Any, Optional, Tuple
import psycopg
from psycopg.errors import UniqueViolation

from opentelemetry import trace
logger = logging.getLogger(__name__)
tracer = trace.get_tracer(__name__)

from .base import GraphStorage
from .connector import DatabaseConnector  # Importiamo l'interfaccia


class PostgresGraphStorage(GraphStorage):
    def __init__(self, connector: DatabaseConnector, vector_dim: int = 1536):
        """
        Dependency Injection: Il connettore decide la strategia (Pool vs Single).
        """
        self.connector = connector
        self.vector_dim = vector_dim
        
        # Logghiamo solo il fatto che siamo pronti, non i dettagli del pool
        logger.info(f"🐘 PostgresStorage initialized (Vector Dim: {vector_dim})")

    def close(self):
        self.connector.close()


    # ==========================================
    # 1. IDENTITY & LIFECYCLE
    # ==========================================

    def ensure_repository(self, url: str, branch: str, name: str) -> str:
        sql = """
            INSERT INTO repositories (id, url, branch, name)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (url, branch) DO UPDATE 
            SET name = EXCLUDED.name, updated_at = NOW()
            RETURNING id
        """
        repo_id = str(uuid.uuid4())
        with self.connector.get_connection() as conn:
            res = conn.execute(sql, (repo_id, url, branch, name)).fetchone()
            return str(res['id'])
            
    def create_snapshot(self, repository_id: str, commit_hash: str, force_new: bool = False) -> Tuple[Optional[str], bool]:
        new_id = str(uuid.uuid4())
        
        try:
            with self.connector.get_connection() as conn:
                if not force_new:
                    row = conn.execute("""
                        SELECT id, status FROM snapshots 
                        WHERE repository_id = %s AND commit_hash = %s AND status = 'completed'
                        ORDER BY created_at DESC LIMIT 1
                    """, (repository_id, commit_hash)).fetchone()
                    
                    if row:
                        logger.info(f"✅ Snapshot esistente trovato: {row['id']}")
                        return str(row['id']), False

                conn.execute("""
                    INSERT INTO snapshots (id, repository_id, commit_hash, status, created_at)
                    VALUES (%s, %s, %s, 'indexing', NOW())
                """, (new_id, repository_id, commit_hash))
                
                logger.info(f"🔒 Lock acquisito: Inizio indicizzazione snapshot {new_id}")
                return new_id, True

        except psycopg.errors.UniqueViolation:
            logger.info(f"⏳ Repo occupata. Imposto dirty flag per {repository_id}")
            with self.connector.get_connection() as conn:
                conn.execute("""
                    UPDATE repositories 
                    SET reindex_requested_at = NOW() 
                    WHERE id = %s
                """, (repository_id,))
            return None, False
        
    def check_and_reset_reindex_flag(self, repository_id: str) -> bool:
        with self.connector.get_connection() as conn:
            row = conn.execute("""
                UPDATE repositories 
                SET reindex_requested_at = NULL
                WHERE id = %s AND reindex_requested_at IS NOT NULL
                RETURNING id
            """, (repository_id,)).fetchone()
            return row is not None
    
    def activate_snapshot(self, repository_id: str, snapshot_id: str, stats: Dict[str, Any] = None, manifest: Dict[str, Any] = None):
        with self.connector.get_connection() as conn:
            with conn.transaction():
                conn.execute("""
                    UPDATE snapshots 
                    SET status='completed', completed_at=NOW(), stats=%s, file_manifest=%s 
                    WHERE id=%s
                """, (json.dumps(stats or {}), json.dumps(manifest or {}), snapshot_id))
                
                conn.execute("""
                    UPDATE repositories 
                    SET current_snapshot_id=%s, updated_at=NOW()
                    WHERE id=%s
                """, (snapshot_id, repository_id))
        logger.info(f"🚀 SNAPSHOT ACTIVATED: {snapshot_id}")

    def fail_snapshot(self, snapshot_id: str, error: str):
        with self.connector.get_connection() as conn:
            conn.execute("UPDATE snapshots SET status='failed', stats=jsonb_build_object('error', %s::text) WHERE id=%s", (error, snapshot_id))

    def prune_snapshot(self, snapshot_id: str):
        logger.info(f"🧹 Pruning snapshot data: {snapshot_id}")
        with self.connector.get_connection() as conn:
            conn.execute("DELETE FROM files WHERE snapshot_id = %s", (snapshot_id,))

    def get_active_snapshot_id(self, repository_id: str) -> Optional[str]:
        with self.connector.get_connection() as conn:
            row = conn.execute("SELECT current_snapshot_id FROM repositories WHERE id=%s", (repository_id,)).fetchone()
            return str(row['current_snapshot_id']) if row and row['current_snapshot_id'] else None

    def get_repository(self, repo_id: str) -> Optional[Dict[str, Any]]:
        with self.connector.get_connection() as conn:
            return conn.execute("SELECT * FROM repositories WHERE id=%s", (repo_id,)).fetchone()
            
    def get_snapshot_manifest(self, snapshot_id: str) -> Dict[str, Any]:
        sql = "SELECT file_manifest FROM snapshots WHERE id = %s"
        with self.connector.get_connection() as conn:
            row = conn.execute(sql, (snapshot_id,)).fetchone()
            return row['file_manifest'] if row and row['file_manifest'] else {}

    # ==========================================
    # 2. WRITE OPERATIONS (OPTIMIZED)
    # ==========================================

    def add_files(self, files: List[Any]):
        if not files: return
        with self.connector.get_connection() as conn:
            with conn.cursor() as cur:
                data = [f.to_dict() for f in files]
                cur.executemany("""
                    INSERT INTO files (id, snapshot_id, commit_hash, file_hash, path, language, size_bytes, category, indexed_at, parsing_status, parsing_error)
                    VALUES (%(id)s, %(snapshot_id)s, %(commit_hash)s, %(file_hash)s, %(path)s, %(language)s, %(size_bytes)s, %(category)s, %(indexed_at)s, %(parsing_status)s, %(parsing_error)s)
                    ON CONFLICT (snapshot_id, path) DO UPDATE 
                    SET file_hash=EXCLUDED.file_hash, parsing_status=EXCLUDED.parsing_status
                """, data)

    def add_nodes(self, nodes: List[Any]):
        """
        Metodo standard per inserire nodi con gestione conflitti (sicuro per retries).
        """
        if not nodes: return
        with self.connector.get_connection() as conn:
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

    def add_nodes_fast(self, nodes: List[Any]):
        """
        [NEW] Versione ottimizzata usando il protocollo COPY.
        Estremamente veloce per bulk inserts (nuovi snapshot).
        ATTENZIONE: Fallisce se ci sono duplicati (non supporta ON CONFLICT).
        """
        if not nodes: return
        
        def data_generator():
            for n in nodes:
                d = n.to_dict()
                meta = json.dumps(d.get('metadata', {}))
                bs, be = d['byte_range']
                # Deve rispettare l'ordine delle colonne nel comando COPY sotto
                yield (
                    d['id'], d.get('file_id'), d['file_path'], 
                    d['start_line'], d['end_line'], bs, be, 
                    d.get('chunk_hash', ''), be - bs, meta
                )

        sql = """
            COPY nodes (id, file_id, file_path, start_line, end_line, byte_start, byte_end, chunk_hash, size, metadata)
            FROM STDIN
        """
        
        try:
            with self.connector.get_connection() as conn:
                with conn.cursor() as cur:
                    with cur.copy(sql) as copy:
                        for row in data_generator():
                            copy.write_row(row)
        except Exception as e:
            logger.error(f"❌ COPY failed in add_nodes_fast: {e}")
            raise e

    def add_contents(self, contents: List[Any]):
        if not contents: return
        with self.connector.get_connection() as conn:
            with conn.cursor() as cur:
                data = [c.to_dict() for c in contents]
                cur.executemany("INSERT INTO contents (chunk_hash, content) VALUES (%(chunk_hash)s, %(content)s) ON CONFLICT (chunk_hash) DO NOTHING", data)

    def add_edge(self, source_id, target_id, relation_type, metadata):
        with self.connector.get_connection() as conn:
            conn.execute("INSERT INTO edges (source_id, target_id, relation_type, metadata) VALUES (%s, %s, %s, %s)", (source_id, target_id, relation_type, json.dumps(metadata)))

    def save_embeddings(self, vector_documents: List[Dict[str, Any]]):
        if not vector_documents: return
        with self.connector.get_connection() as conn:
            with conn.cursor() as cur:
                cur.executemany("""
                    INSERT INTO node_embeddings (
                        id, chunk_id, snapshot_id, vector_hash, model_name, created_at, 
                        file_path, language, category, start_line, end_line, embedding
                    ) VALUES (
                        %(id)s, %(chunk_id)s, %(snapshot_id)s, %(vector_hash)s, %(model_name)s, %(created_at)s,
                        %(file_path)s, %(language)s, %(category)s, %(start_line)s, %(end_line)s, %(embedding)s
                    )
                    ON CONFLICT (id) DO NOTHING
                """, vector_documents)

    def add_search_index(self, search_docs: List[Dict[str, Any]]):
        if not search_docs: return
        with self.connector.get_connection() as conn:
            with conn.cursor() as cur:
                cur.executemany("""
                    INSERT INTO nodes_fts (node_id, file_path, semantic_tags, content, search_vector)
                    VALUES (
                        %(node_id)s, %(file_path)s, %(tags)s, %(content)s,
                        setweight(to_tsvector('english', %(tags)s), 'A') || 
                        setweight(to_tsvector('english', %(content)s), 'B')
                    )
                    ON CONFLICT (node_id) DO UPDATE 
                    SET search_vector = EXCLUDED.search_vector, content = EXCLUDED.content
                """, search_docs)

    # ==========================================
    # 3. READ OPERATIONS
    # ==========================================

    def _build_filter_clause(self, filters: Dict[str, Any], col_map: Dict[str, str]) -> Tuple[str, List[Any]]:
        if not filters: return "", []
        clauses = []
        params = []
        def as_list(val): return val if isinstance(val, list) else [val]

        if filters.get("path_prefix"):
            col = col_map.get('path')
            if col:
                paths = as_list(filters["path_prefix"])
                if paths:
                    or_clauses = []
                    for p in paths:
                        or_clauses.append(f"{col} LIKE %s")
                        params.append(p.rstrip('/') + '%')
                    clauses.append(f"({' OR '.join(or_clauses)})")

        if filters.get("language"):
            col = col_map.get('lang')
            if col:
                langs = as_list(filters["language"])
                if langs:
                    clauses.append(f"{col} = ANY(%s)")
                    params.append(langs)
            
        if filters.get("exclude_language"):
            col = col_map.get('lang')
            if col:
                ex_langs = as_list(filters["exclude_language"])
                if ex_langs:
                    clauses.append(f"{col} != ALL(%s)")
                    params.append(ex_langs)

        col_meta = col_map.get('meta')
        if col_meta:
            if filters.get("role"): 
                roles = as_list(filters["role"])
                role_clauses = []
                for r in roles:
                    role_clauses.append(f"{col_meta} @> %s::jsonb")
                    params.append(json.dumps({"semantic_matches": [{"category": "role", "value": r}]}))
                if role_clauses:
                    clauses.append(f"({' OR '.join(role_clauses)})")
            
            if filters.get("exclude_role"): 
                ex_roles = as_list(filters["exclude_role"])
                ex_clauses = []
                for r in ex_roles:
                    ex_clauses.append(f"{col_meta} @> %s::jsonb")
                    params.append(json.dumps({"semantic_matches": [{"category": "role", "value": r}]}))
                if ex_clauses:
                    clauses.append(f"NOT ({' OR '.join(ex_clauses)})")

        col_cat = col_map.get('cat')
        if col_cat:
            if filters.get("category"):
                cats = as_list(filters["category"])
                clauses.append(f"{col_cat} = ANY(%s)")
                params.append(cats)
            
            if filters.get("exclude_category"):
                ex_cats = as_list(filters["exclude_category"])
                clauses.append(f"{col_cat} != ALL(%s)")
                params.append(ex_cats)

        if not clauses: return "", []
        return " AND " + " AND ".join(clauses), params

    def search_vectors(self, query_vector: List[float], limit: int, snapshot_id: str, filters: Dict[str, Any] = None) -> List[Dict[str, Any]]:

        if not snapshot_id: raise ValueError("snapshot_id mandatory.")

        sql = """
            SELECT ne.chunk_id, ne.file_path, ne.start_line, ne.end_line, ne.snapshot_id, n.metadata, c.content, ne.language, 
                (ne.embedding <=> %s::vector) as distance
            FROM node_embeddings ne 
            JOIN nodes n ON ne.chunk_id = n.id 
            JOIN contents c ON n.chunk_hash = c.chunk_hash
            WHERE ne.snapshot_id = %s
        """
        params = [query_vector, snapshot_id]
        col_map = {'path': 'ne.file_path', 'lang': 'ne.language', 'cat': 'ne.category', 'meta': 'n.metadata'}
        
        filter_sql, filter_params = self._build_filter_clause(filters, col_map)
        sql += filter_sql
        params.extend(filter_params)
        
        sql += " ORDER BY distance ASC LIMIT %s"
        params.append(limit)
        
        with tracer.start_as_current_span("db.search.vectors") as span:

            span.set_attribute("search.limit", limit)
            span.set_attribute("snapshot.id", snapshot_id)
            if filters:
                span.set_attribute("search.filters_keys", list(filters.keys()))

            with self.connector.get_connection() as conn:
                results = []
                # Qui misuriamo implicitamente anche il tempo di esecuzione della query
                for row in conn.execute(sql, params).fetchall():
                    results.append({
                        "id": str(row['chunk_id']), "file_path": row['file_path'], 
                        "start_line": row['start_line'], "end_line": row['end_line'],
                        "snapshot_id": str(row['snapshot_id']), "metadata": row['metadata'], 
                        "content": row['content'], "language": row['language'], "score": 1 - row['distance']
                    })
                
                span.set_attribute("search.results_count", len(results))
                return results

    def search_fts(self, query: str, limit: int, snapshot_id: str, filters: Dict[str, Any] = None) -> List[Dict[str, Any]]:
        if not snapshot_id: raise ValueError("snapshot_id mandatory.")
        sql = """
            SELECT fts.node_id, fts.file_path, n.start_line, n.end_line, fts.content, f.snapshot_id, n.metadata, f.language,
                   ts_rank(fts.search_vector, websearch_to_tsquery('english', %s)) as rank
            FROM nodes_fts fts 
            JOIN nodes n ON fts.node_id = n.id 
            JOIN files f ON n.file_id = f.id
            WHERE fts.search_vector @@ websearch_to_tsquery('english', %s) 
            AND f.snapshot_id = %s
        """
        params = [query, query, snapshot_id]
        col_map = {'path': 'f.path', 'lang': 'f.language', 'cat': 'f.category', 'meta': 'n.metadata'}
        
        filter_sql, filter_params = self._build_filter_clause(filters, col_map)
        sql += filter_sql
        params.extend(filter_params)
        
        sql += " ORDER BY rank DESC LIMIT %s"
        params.append(limit)

        try:
            with self.connector.get_connection() as conn:
                results = []
                for row in conn.execute(sql, params).fetchall():
                    results.append({
                        "id": str(row['node_id']), "file_path": row['file_path'], 
                        "start_line": row['start_line'], "end_line": row['end_line'],
                        "score": row['rank'], "content": row['content'], 
                        "snapshot_id": str(row['snapshot_id']), "metadata": row['metadata'], "language": row['language']
                    })
                return results
        except Exception as e:
            logger.error(f"Postgres FTS Error: {e}")
            return []

    # ==========================================
    # 4. UTILS & NAVIGATION
    # ==========================================

    def find_chunk_id(self, file_path: str, byte_range: List[int], snapshot_id: str) -> Optional[str]:
        if not byte_range or not snapshot_id: return None
        sql = """
            SELECT n.id FROM nodes n JOIN files f ON n.file_id = f.id 
            WHERE f.path = %s AND f.snapshot_id = %s
              AND n.byte_start <= %s + 1 AND n.byte_end >= %s - 1
            ORDER BY n.size ASC LIMIT 1
        """
        with self.connector.get_connection() as conn:
            row = conn.execute(sql, (file_path, snapshot_id, byte_range[0], byte_range[1])).fetchone()
            return str(row['id']) if row else None

    def get_incoming_references(self, target_node_id: str, limit: int = 50) -> List[Dict[str, Any]]:
        with self.connector.get_connection() as conn:
            res = []
            for r in conn.execute("SELECT s.id, s.file_path, s.start_line, e.relation_type, e.metadata FROM edges e JOIN nodes s ON e.source_id=s.id WHERE e.target_id=%s AND e.relation_type IN ('calls', 'references', 'imports', 'instantiates') ORDER BY s.file_path, s.start_line LIMIT %s", (target_node_id, limit)).fetchall():
                res.append({"source_id": str(r['id']), "file": r['file_path'], "line": r['start_line'], "relation": r['relation_type'], "context_snippet": r['metadata'].get("description", "")})
            return res

    def get_outgoing_calls(self, source_node_id: str, limit: int = 50) -> List[Dict[str, Any]]:
        with self.connector.get_connection() as conn:
            res = []
            for r in conn.execute("SELECT t.id, t.file_path, t.start_line, e.relation_type, e.metadata FROM edges e JOIN nodes t ON e.target_id=t.id WHERE e.source_id=%s AND e.relation_type IN ('calls', 'instantiates', 'imports') ORDER BY t.file_path, t.start_line LIMIT %s", (source_node_id, limit)).fetchall():
                res.append({"target_id": str(r['id']), "file": r['file_path'], "line": r['start_line'], "relation": r['relation_type'], "symbol": r['metadata'].get("symbol", "")})
            return res

    def get_context_neighbors(self, node_id: str):
        res = {"parents": [], "calls": []}
        with self.connector.get_connection() as conn:
            for r in conn.execute("SELECT t.id, t.file_path, t.start_line, e.metadata, t.metadata FROM edges e JOIN nodes t ON e.target_id=t.id WHERE e.source_id=%s AND e.relation_type='child_of'", (node_id,)).fetchall():
                res["parents"].append({"id": str(r['id']), "file_path": r['file_path'], "start_line": r['start_line'], "edge_meta": r['metadata'], "metadata": r['metadata']})
            for r in conn.execute("SELECT t.id, t.file_path, e.metadata FROM edges e JOIN nodes t ON e.target_id=t.id WHERE e.source_id=%s AND e.relation_type IN ('calls','references') LIMIT 15", (node_id,)).fetchall():
                res["calls"].append({"id": str(r['id']), "symbol": r['metadata'].get("symbol", "unknown")})
        return res

    def get_neighbor_chunk(self, node_id: str, direction: str = "next") -> Optional[Dict[str, Any]]:
        with self.connector.get_connection() as conn:
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

    def get_neighbor_metadata(self, node_id: str) -> Dict[str, Any]:
        info = {"next": None, "prev": None, "parent": None}
        with self.connector.get_connection() as conn:
            curr = conn.execute("SELECT file_id, start_line, end_line FROM nodes WHERE id=%s", (node_id,)).fetchone()
            if not curr: return info
            fid, s, e = curr['file_id'], curr['start_line'], curr['end_line']
            rn = conn.execute("SELECT id, metadata FROM nodes WHERE file_id=%s AND start_line >= %s AND id!=%s ORDER BY start_line ASC LIMIT 1", (fid, e, node_id)).fetchone()
            if rn: info["next"] = self._format_nav_node(rn)
            rp = conn.execute("SELECT id, metadata FROM nodes WHERE file_id=%s AND end_line <= %s AND id!=%s ORDER BY end_line DESC LIMIT 1", (fid, s, node_id)).fetchone()
            if rp: info["prev"] = self._format_nav_node(rp)
            rpar = conn.execute("SELECT t.id, t.metadata FROM edges e JOIN nodes t ON e.target_id=t.id WHERE e.source_id=%s AND e.relation_type='child_of' LIMIT 1", (node_id,)).fetchone()
            if rpar: info["parent"] = self._format_nav_node(rpar)
        return info

    def _format_nav_node(self, row):
        meta = row['metadata']
        if isinstance(meta, str): meta = json.loads(meta)
        matches = meta.get('semantic_matches', [])
        label = "Code Block"
        for m in matches:
            if m.get('category') == 'role':
                label = m.get('label') or m.get('value'); break 
            if m.get('category') == 'type':
                label = m.get('label') or m.get('value')
        return {"id": str(row['id']), "label": label}

    def get_nodes_to_embed(self, snapshot_id: str, model_name: str, batch_size: int = 2000):
        sql = """
            SELECT n.id, n.file_path, n.chunk_hash, n.start_line, n.end_line, n.metadata,
                   f.language, f.category 
            FROM files f JOIN nodes n ON f.id = n.file_id
            LEFT JOIN node_embeddings ne ON (n.id = ne.chunk_id AND ne.model_name = %s)
            WHERE f.snapshot_id = %s AND ne.id IS NULL
        """
        cursor_name = f"embed_stream_{uuid.uuid4().hex}"
        with self.connector.get_connection() as conn:
            with conn.transaction():
                with conn.cursor(name=cursor_name) as cur:
                    cur.itersize = batch_size
                    cur.execute(sql, (model_name, snapshot_id))
                    for r in cur:
                        yield {
                            "id": str(r['id']), "file_path": r['file_path'], "chunk_hash": r['chunk_hash'],
                            "start_line": r['start_line'], "end_line": r['end_line'], "metadata_json": json.dumps(r['metadata']),
                            "snapshot_id": snapshot_id, "language": r['language'], "category": r['category']
                        }

    def get_vectors_by_hashes(self, vector_hashes: List[str], model_name: str) -> Dict[str, List[float]]:
        if not vector_hashes: return {}
        res = {}
        with self.connector.get_connection() as conn:
            query = "SELECT DISTINCT ON (vector_hash) vector_hash, embedding FROM node_embeddings WHERE vector_hash = ANY(%s) AND model_name = %s"
            for r in conn.execute(query, (vector_hashes, model_name)).fetchall():
                if r['embedding'] is not None: res[r['vector_hash']] = r['embedding']
        return res

    def get_incoming_definitions_bulk(self, node_ids: List[str]) -> Dict[str, List[str]]:
        if not node_ids: return {}
        res = {}
        with self.connector.get_connection() as conn:
            for i in range(0, len(node_ids), 500):
                batch = node_ids[i:i+500]
                for r in conn.execute("SELECT target_id, metadata FROM edges WHERE target_id = ANY(%s) AND relation_type='calls'", (batch,)).fetchall():
                    sym = r['metadata'].get("symbol")
                    if sym:
                        tid = str(r['target_id'])
                        if tid not in res: res[tid] = set()
                        res[tid].add(sym)
        return {k: list(v) for k, v in res.items()}

    def get_contents_bulk(self, chunk_hashes: List[str]) -> Dict[str, str]:
        if not chunk_hashes: return {}
        res = {}
        with self.connector.get_connection() as conn:
            for i in range(0, len(chunk_hashes), 500):
                batch = chunk_hashes[i:i+500]
                for r in conn.execute("SELECT chunk_hash, content FROM contents WHERE chunk_hash = ANY(%s)", (batch,)).fetchall():
                    res[r['chunk_hash']] = r['content']
        return res

    def list_file_paths(self, snapshot_id: str) -> List[str]:
        sql = "SELECT path FROM files WHERE snapshot_id = %s ORDER BY path"
        with self.connector.get_connection() as conn:
            return [r['path'] for r in conn.execute(sql, (snapshot_id,)).fetchall()]

    def get_file_content_range(self, snapshot_id: str, file_path: str, start_line: int = None, end_line: int = None) -> Optional[str]:
        sl = start_line if start_line is not None else 0
        el = end_line if end_line is not None else 999999
        sql = """
            SELECT c.content, n.start_line
            FROM nodes n JOIN files f ON n.file_id = f.id JOIN contents c ON n.chunk_hash = c.chunk_hash
            WHERE f.snapshot_id = %s AND f.path = %s AND n.end_line >= %s AND n.start_line <= %s
            ORDER BY n.byte_start ASC
        """
        with self.connector.get_connection() as conn:
            rows = conn.execute(sql, (snapshot_id, file_path, sl, el)).fetchall()
        if not rows:
            with self.connector.get_connection() as conn:
                exists = conn.execute("SELECT 1 FROM files WHERE snapshot_id=%s AND path=%s", (snapshot_id, file_path)).fetchone()
            if exists: return "" 
            return None 
        full_blob = "".join([r['content'] for r in rows])
        first_chunk_start = rows[0]['start_line']
        lines = full_blob.splitlines(keepends=True)
        rel_start = max(0, sl - first_chunk_start) if start_line else 0
        if end_line: rel_end = min(len(lines), el - first_chunk_start + 1)
        else: rel_end = len(lines)
        return "".join(lines[rel_start:rel_end])

    def get_stats(self):
        with self.connector.get_connection() as conn:
            return {
                "files": conn.execute("SELECT COUNT(*) as c FROM files").fetchone()['c'],
                "total_nodes": conn.execute("SELECT COUNT(*) as c FROM nodes").fetchone()['c'],
                "embeddings": conn.execute("SELECT COUNT(*) as c FROM node_embeddings").fetchone()['c'],
                "snapshots": conn.execute("SELECT COUNT(*) as c FROM snapshots").fetchone()['c'],
                "repos": conn.execute("SELECT COUNT(*) as c FROM repositories").fetchone()['c']
            }
        
    # ==========================================
    # 2. WRITE OPERATIONS (RAW TUPLES & COPY)
    # ==========================================

    def add_files_raw(self, files_tuples: List[Tuple]):
        """Inserimento massivo files."""
        if not files_tuples: return
        with self.connector.get_connection() as conn:
            with conn.cursor() as cur:
                cur.executemany("""
                    INSERT INTO files (id, snapshot_id, commit_hash, file_hash, path, language, size_bytes, category, indexed_at, parsing_status, parsing_error)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (snapshot_id, path) DO UPDATE 
                    SET file_hash=EXCLUDED.file_hash, parsing_status=EXCLUDED.parsing_status
                """, files_tuples)

    def add_nodes_raw(self, nodes_tuples: List[Tuple]):
        """Inserimento massivo nodi via COPY (Velocissimo)."""
        if not nodes_tuples: return
        sql = """
            COPY nodes (id, file_id, file_path, start_line, end_line, byte_start, byte_end, chunk_hash, size, metadata)
            FROM STDIN
        """
        with tracer.start_as_current_span("db.write.nodes_copy") as span:
            batch_size = len(nodes_tuples)
            span.set_attribute("db.batch_size", batch_size)
            span.set_attribute("db.table", "nodes")
            
            try:
                with self.connector.get_connection() as conn:
                    with conn.cursor() as cur:
                        with cur.copy(sql) as copy:
                            for row in nodes_tuples:
                                copy.write_row(row)
                                
            except Exception as e:
                span.record_exception(e)
                span.set_status(trace.Status(trace.StatusCode.ERROR))
                logger.error(f"❌ COPY failed in add_nodes_raw: {e}")
                raise e

    def add_contents_raw(self, contents_tuples: List[Tuple]):
        """Inserimento massivo contenuti."""
        if not contents_tuples: return
        with self.connector.get_connection() as conn:
            with conn.cursor() as cur:
                cur.executemany("INSERT INTO contents (chunk_hash, content) VALUES (%s, %s) ON CONFLICT (chunk_hash) DO NOTHING", contents_tuples)

    def add_relations_raw(self, rels_tuples: List[Tuple]):
        """Inserimento massivo relazioni."""
        if not rels_tuples: return
        with self.connector.get_connection() as conn:
            with conn.cursor() as cur:
                 cur.executemany("INSERT INTO edges (source_id, target_id, relation_type, metadata) VALUES (%s, %s, %s, %s)", rels_tuples)

    def ingest_scip_relations(self, relations_tuples: List[Tuple], snapshot_id: str):
        """
        Ingestion ad alte prestazioni delle relazioni SCIP.
        
        Risolve gli ID dei nodi direttamente nel DB usando JOIN spaziali sui range di byte.
        Trova automaticamente il nodo 'più specifico' (più piccolo) che contiene il range della relazione.
        
        Args:
            relations_tuples: Lista di tuple nel formato:
            (source_path, s_start, s_end, target_path, t_start, t_end, rel_type, meta_json)
            snapshot_id: ID dello snapshot corrente per limitare i join.
        """
        if not relations_tuples: return

        # Usiamo ON COMMIT DROP: la tabella vive solo finché la transazione è aperta.
        ddl_temp = """
            CREATE TEMP TABLE IF NOT EXISTS temp_scip_staging (
                s_path TEXT, s_start INT, s_end INT,
                t_path TEXT, t_start INT, t_end INT,
                rel_type TEXT, meta JSONB
            ) ON COMMIT DROP; 
        """
        
        sql_resolve = """
            INSERT INTO edges (source_id, target_id, relation_type, metadata)
            SELECT DISTINCT ON (t.s_path, t.s_start, t.t_path, t.t_start, t.rel_type)
                ns.id, nt.id, t.rel_type, t.meta
            FROM temp_scip_staging t
            JOIN files fs ON fs.snapshot_id = %s AND fs.path = t.s_path
            JOIN nodes ns ON ns.file_id = fs.id 
                AND ns.byte_start <= t.s_start AND ns.byte_end >= t.s_end
            JOIN files ft ON ft.snapshot_id = %s AND ft.path = t.t_path
            JOIN nodes nt ON nt.file_id = ft.id 
                AND nt.byte_start <= t.t_start AND nt.byte_end >= t.t_end
            
            WHERE ns.id != nt.id -- <--- STOP SELF LOOPS
            
            ORDER BY t.s_path, t.s_start, t.t_path, t.t_start, t.rel_type, 
                     ns.size ASC, nt.size ASC
        """

        # CRUCIALE: Usiamo conn.transaction()
        # Questo disabilita temporaneamente l'autocommit per questo blocco.
        # Garantisce che la TEMP table sopravviva tra il CREATE e il COPY,
        # e venga eliminata automaticamente (ON COMMIT DROP) all'uscita del blocco.
        with tracer.start_as_current_span("db.scip.ingest_transaction") as span:
            span.set_attribute("db.batch_size", len(relations_tuples))
            span.set_attribute("snapshot.id", snapshot_id)

            try:
                with self.connector.get_connection() as conn:
                    with conn.transaction(): 
                        with conn.cursor() as cur:
                            cur.execute(ddl_temp)
                            
                            # [OTEL] Fase 1: Caricamento Dati Raw (I/O Bound)
                            with tracer.start_as_current_span("db.scip.copy_temp") as copy_span:
                                copy_span.set_attribute("row.count", len(relations_tuples))
                                with cur.copy("COPY temp_scip_staging FROM STDIN") as copy:
                                    for row in relations_tuples:
                                        copy.write_row(row)
                            
                            # [OTEL] Fase 2: Risoluzione Relazionale (CPU/Join Bound)
                            with tracer.start_as_current_span("db.scip.resolve_query") as resolve_span:
                                cur.execute(sql_resolve, (snapshot_id, snapshot_id))
                                resolve_span.set_attribute("edges.created", cur.rowcount)
                            
                            logger.info(f"🔗 SCIP Bulk Ingestion: {cur.rowcount} edges created.")
                            
            except Exception as e:
                span.record_exception(e)
                span.set_status(trace.Status(trace.StatusCode.ERROR))
                logger.error(f"❌ SCIP Ingestion Failed: {e}")
                raise e
        
