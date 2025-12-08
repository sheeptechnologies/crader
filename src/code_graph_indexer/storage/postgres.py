import os
import json
import logging
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
        with self.pool.connection() as conn:
            res = conn.execute(sql, (repo_id, url, branch, name)).fetchone()
            return str(res['id'])

    def create_snapshot(self, repository_id: str, commit_hash: str) -> Tuple[str, bool]:
        new_id = str(uuid.uuid4())
        with self.pool.connection() as conn:
            try:
                res = conn.execute("""
                    INSERT INTO snapshots (id, repository_id, commit_hash, status, created_at)
                    VALUES (%s, %s, %s, 'indexing', NOW())
                    ON CONFLICT (repository_id, commit_hash) DO NOTHING
                    RETURNING id
                """, (new_id, repository_id, commit_hash)).fetchone()
                if res:
                    logger.info(f"📸 Snapshot CREATO: {new_id} (Commit: {commit_hash[:8]})")
                    return str(res['id']), True
            except psycopg.errors.UniqueViolation:
                pass 

            row = conn.execute("SELECT id, status FROM snapshots WHERE repository_id = %s AND commit_hash = %s", (repository_id, commit_hash)).fetchone()
            if not row:
                raise Exception(f"Critical: Snapshot consistency error for repo {repository_id}")
            
            existing_id = str(row['id'])
            if row['status'] == 'failed':
                logger.warning(f"♻️ Snapshot {existing_id} was 'failed'. Purging data & Resetting.")
                # [FIX] Pulizia dati orfani per evitare collisioni di ID
                conn.execute("DELETE FROM files WHERE snapshot_id = %s", (existing_id,))
                conn.execute("UPDATE snapshots SET status='indexing', created_at=NOW() WHERE id=%s", (existing_id,))
                return existing_id, True 
                
            return existing_id, False

    def activate_snapshot(self, repository_id: str, snapshot_id: str, stats: Dict[str, Any] = None):
        with self.pool.connection() as conn:
            with conn.transaction():
                conn.execute("UPDATE snapshots SET status='completed', completed_at=NOW(), stats=%s WHERE id=%s", (json.dumps(stats or {}), snapshot_id))
                conn.execute("UPDATE repositories SET current_snapshot_id=%s, updated_at=NOW() WHERE id=%s", (snapshot_id, repository_id))
        logger.info(f"🚀 SNAPSHOT ACTIVATED: {snapshot_id}")

    def fail_snapshot(self, snapshot_id: str, error: str):
        with self.pool.connection() as conn:
            # [FIX] Casting ::text per evitare IndeterminateDatatype
            conn.execute("UPDATE snapshots SET status='failed', stats=jsonb_build_object('error', %s::text) WHERE id=%s", (error, snapshot_id))

    def prune_snapshot(self, snapshot_id: str):
        """
        Rimuove file e nodi di uno snapshot.
        Essenziale per 'force=True' o retry.
        """
        logger.info(f"🧹 Pruning snapshot data: {snapshot_id}")
        with self.pool.connection() as conn:
            # ON DELETE CASCADE sulle FK farà il resto per nodi ed edges
            conn.execute("DELETE FROM files WHERE snapshot_id = %s", (snapshot_id,))

    def get_active_snapshot_id(self, repository_id: str) -> Optional[str]:
        with self.pool.connection() as conn:
            row = conn.execute("SELECT current_snapshot_id FROM repositories WHERE id=%s", (repository_id,)).fetchone()
            return str(row['current_snapshot_id']) if row and row['current_snapshot_id'] else None

    def get_repository(self, repo_id: str) -> Optional[Dict[str, Any]]:
        with self.pool.connection() as conn:
            return conn.execute("SELECT * FROM repositories WHERE id=%s", (repo_id,)).fetchone()

    # ==========================================
    # 2. WRITE OPERATIONS
    # ==========================================

    def add_files(self, files: List[Any]):
        if not files: return
        with self.pool.connection() as conn:
            with conn.cursor() as cur:
                data = [f.to_dict() for f in files]
                cur.executemany("""
                    INSERT INTO files (id, snapshot_id, commit_hash, file_hash, path, language, size_bytes, category, indexed_at, parsing_status, parsing_error)
                    VALUES (%(id)s, %(snapshot_id)s, %(commit_hash)s, %(file_hash)s, %(path)s, %(language)s, %(size_bytes)s, %(category)s, %(indexed_at)s, %(parsing_status)s, %(parsing_error)s)
                    ON CONFLICT (snapshot_id, path) DO UPDATE 
                    SET file_hash=EXCLUDED.file_hash, parsing_status=EXCLUDED.parsing_status
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
                    SET search_vector = EXCLUDED.search_vector, content = EXCLUDED.content
                """, search_docs)

    # ==========================================
    # 3. READ OPERATIONS
    # ==========================================

    def find_chunk_id(self, file_path: str, byte_range: List[int], snapshot_id: str) -> Optional[str]:
        if not byte_range or not snapshot_id: return None
        sql = """
            SELECT n.id FROM nodes n JOIN files f ON n.file_id = f.id 
            WHERE f.path = %s AND f.snapshot_id = %s
              AND n.byte_start <= %s + 1 AND n.byte_end >= %s - 1
            ORDER BY n.size ASC LIMIT 1
        """
        with self.pool.connection() as conn:
            row = conn.execute(sql, (file_path, snapshot_id, byte_range[0], byte_range[1])).fetchone()
            return str(row['id']) if row else None

    def search_vectors(self, query_vector: List[float], limit: int, snapshot_id: str, filters: Dict[str, Any] = None) -> List[Dict[str, Any]]:
        if not snapshot_id: raise ValueError("snapshot_id is mandatory.")
        sql = """
            SELECT ne.chunk_id, ne.file_path, ne.start_line, ne.end_line, ne.snapshot_id, n.metadata, c.content, ne.language, (ne.embedding <=> %s::vector) as distance
            FROM node_embeddings ne JOIN nodes n ON ne.chunk_id = n.id JOIN contents c ON n.chunk_hash = c.chunk_hash
            WHERE ne.snapshot_id = %s
        """
        params = [query_vector, snapshot_id]
        filter_sql, filter_params = self._build_filter_clause(filters)
        sql += filter_sql + " ORDER BY distance ASC LIMIT %s"
        params.extend(filter_params)
        params.append(limit)

        with self.pool.connection() as conn:
            results = []
            for row in conn.execute(sql, params).fetchall():
                results.append({
                    "id": str(row['chunk_id']), "file_path": row['file_path'], "start_line": row['start_line'], "end_line": row['end_line'],
                    "snapshot_id": str(row['snapshot_id']), "metadata": row['metadata'], "content": row['content'], "language": row['language'], "score": 1 - row['distance']
                })
            return results

    def search_fts(self, query: str, limit: int, snapshot_id: str, filters: Dict[str, Any] = None) -> List[Dict[str, Any]]:
        if not snapshot_id: raise ValueError("snapshot_id is mandatory.")
        sql = """
            SELECT fts.node_id, fts.file_path, n.start_line, n.end_line, fts.content, f.snapshot_id, n.metadata, f.language, ts_rank(fts.search_vector, websearch_to_tsquery('english', %s)) as rank
            FROM nodes_fts fts JOIN nodes n ON fts.node_id = n.id JOIN files f ON n.file_id = f.id
            WHERE fts.search_vector @@ websearch_to_tsquery('english', %s) AND f.snapshot_id = %s
        """
        params = [query, query, snapshot_id]
        filter_sql, filter_params = self._build_filter_clause(filters)
        sql += filter_sql + " ORDER BY rank DESC LIMIT %s"
        params.extend(filter_params)
        params.append(limit)

        try:
            with self.pool.connection() as conn:
                results = []
                for row in conn.execute(sql, params).fetchall():
                    results.append({
                        "id": str(row['node_id']), "file_path": row['file_path'], "start_line": row['start_line'], "end_line": row['end_line'],
                        "score": row['rank'], "content": row['content'], "snapshot_id": str(row['snapshot_id']), "metadata": row['metadata'], "language": row['language']
                    })
                return results
        except Exception as e:
            logger.error(f"Postgres FTS Error: {e}")
            return []

    def _build_filter_clause(self, filters: Dict[str, Any]) -> Tuple[str, List[Any]]:
        if not filters: return "", []
        return "", []

    # ==========================================
    # 4. UTILS, BATCHING & NAVIGATION
    # ==========================================

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

    def get_nodes_to_embed(self, snapshot_id: str, model_name: str, batch_size: int = 2000):
        sql = """
            SELECT n.id, n.file_path, n.chunk_hash, n.start_line, n.end_line, n.metadata,
                   f.language, f.category 
            FROM files f JOIN nodes n ON f.id = n.file_id
            LEFT JOIN node_embeddings ne ON (n.id = ne.chunk_id AND ne.model_name = %s)
            WHERE f.snapshot_id = %s AND ne.id IS NULL
        """
        cursor_name = f"embed_stream_{uuid.uuid4().hex}"
        with self.pool.connection() as conn:
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
        with self.pool.connection() as conn:
            query = "SELECT DISTINCT ON (vector_hash) vector_hash, embedding FROM node_embeddings WHERE vector_hash = ANY(%s) AND model_name = %s"
            for r in conn.execute(query, (vector_hashes, model_name)).fetchall():
                if r['embedding'] is not None: res[r['vector_hash']] = r['embedding']
        return res

    def get_context_neighbors(self, node_id: str):
        res = {"parents": [], "calls": []}
        with self.pool.connection() as conn:
            for r in conn.execute("SELECT t.id, t.file_path, t.start_line, e.metadata, t.metadata FROM edges e JOIN nodes t ON e.target_id=t.id WHERE e.source_id=%s AND e.relation_type='child_of'", (node_id,)).fetchall():
                res["parents"].append({"id": str(r['id']), "file_path": r['file_path'], "start_line": r['start_line'], "edge_meta": r['metadata'], "metadata": r['metadata']})
            for r in conn.execute("SELECT t.id, t.file_path, e.metadata FROM edges e JOIN nodes t ON e.target_id=t.id WHERE e.source_id=%s AND e.relation_type IN ('calls','references') LIMIT 15", (node_id,)).fetchall():
                res["calls"].append({"id": str(r['id']), "symbol": r['metadata'].get("symbol", "unknown")})
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

    def get_neighbor_metadata(self, node_id: str) -> Dict[str, Any]:
        info = {"next": None, "prev": None, "parent": None}
        with self.pool.connection() as conn:
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

    def get_neighbor_chunk(self, node_id: str, direction: str = "next") -> Optional[Dict[str, Any]]:
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

    def get_incoming_references(self, target_node_id: str, limit: int = 50) -> List[Dict[str, Any]]:
        with self.pool.connection() as conn:
            res = []
            for r in conn.execute("SELECT s.id, s.file_path, s.start_line, e.relation_type, e.metadata FROM edges e JOIN nodes s ON e.source_id=s.id WHERE e.target_id=%s AND e.relation_type IN ('calls', 'references', 'imports', 'instantiates') ORDER BY s.file_path, s.start_line LIMIT %s", (target_node_id, limit)).fetchall():
                res.append({"source_id": str(r['id']), "file": r['file_path'], "line": r['start_line'], "relation": r['relation_type'], "context_snippet": r['metadata'].get("description", "")})
            return res

    def get_outgoing_calls(self, source_node_id: str, limit: int = 50) -> List[Dict[str, Any]]:
        with self.pool.connection() as conn:
            res = []
            for r in conn.execute("SELECT t.id, t.file_path, t.start_line, e.relation_type, e.metadata FROM edges e JOIN nodes t ON e.target_id=t.id WHERE e.source_id=%s AND e.relation_type IN ('calls', 'instantiates', 'imports') ORDER BY t.file_path, t.start_line LIMIT %s", (source_node_id, limit)).fetchall():
                res.append({"target_id": str(r['id']), "file": r['file_path'], "line": r['start_line'], "relation": r['relation_type'], "symbol": r['metadata'].get("symbol", "")})
            return res

    def _format_nav_node(self, row):
        meta = row['metadata']
        matches = meta.get('semantic_matches', [])
        label = "Code Block"
        for m in matches:
            if m.get('category') == 'role':
                label = m.get('label') or m.get('value'); break 
            if m.get('category') == 'type':
                label = m.get('label') or m.get('value')
        return {"id": str(row['id']), "label": label}
        
    def get_stats(self):
        with self.pool.connection() as conn:
            return {
                "files": conn.execute("SELECT COUNT(*) as c FROM files").fetchone()['c'],
                "snapshots": conn.execute("SELECT COUNT(*) as c FROM snapshots").fetchone()['c'],
                "repos": conn.execute("SELECT COUNT(*) as c FROM repositories").fetchone()['c']
            }