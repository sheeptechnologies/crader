import sqlite3
import os
import json
import logging
import struct
import datetime
import uuid
from typing import List, Dict, Any, Optional, Generator

try:
    import numpy as np
    HAS_NUMPY = True
except ImportError:
    HAS_NUMPY = False

from .base import GraphStorage

logger = logging.getLogger(__name__)

class SqliteGraphStorage(GraphStorage):
    def __init__(self, db_path: str = "sheep_index.db"):
        """
        Inizializza lo storage SQLite.
        Gestisce lo schema database per supportare Indexing, Retrieval e Navigation.
        """
        self._db_file = os.path.abspath(db_path)
        logger.info(f"💾 Storage Database: {self._db_file}")
        
        self._conn = sqlite3.connect(self._db_file, check_same_thread=False)
        self._cursor = self._conn.cursor()
        
        # Ottimizzazioni per scrittura veloce
        self._cursor.execute("PRAGMA synchronous = OFF")
        self._cursor.execute("PRAGMA journal_mode = WAL")
        self._cursor.execute("PRAGMA cache_size = 5000")

        # --- 1. TABELLA REPOSITORIES ---
        self._cursor.execute("""
            CREATE TABLE IF NOT EXISTS repositories (
                id TEXT PRIMARY KEY,
                url TEXT NOT NULL,
                branch TEXT NOT NULL,
                name TEXT,
                last_commit TEXT,
                status TEXT,
                updated_at TEXT,
                local_path TEXT,
                UNIQUE(url, branch)
            )
        """)

        # --- 2. TABELLE STRUTTURALI (Files, Nodes) ---
        self._cursor.execute("""
            CREATE TABLE IF NOT EXISTS files (
                id TEXT PRIMARY KEY, 
                repo_id TEXT,
                commit_hash TEXT, 
                file_hash TEXT,
                path TEXT, 
                language TEXT, 
                size_bytes INTEGER, 
                category TEXT, 
                indexed_at TEXT,
                UNIQUE(repo_id, path)
            )
        """)
        self._cursor.execute("CREATE INDEX IF NOT EXISTS idx_files_path ON files (path)")
        self._cursor.execute("CREATE INDEX IF NOT EXISTS idx_files_repo ON files (repo_id)")

        # [REMOVED] Colonna 'type'. Tutto è in metadata_json ora.
        self._cursor.execute("""
            CREATE TABLE IF NOT EXISTS nodes (
                id TEXT PRIMARY KEY, 
                file_id TEXT,               -- Link forte al file
                file_path TEXT,             -- Ridondante per read veloci
                start_line INTEGER, end_line INTEGER, byte_start INTEGER, byte_end INTEGER,
                chunk_hash TEXT, 
                size INTEGER,
                metadata_json TEXT 
            )
        """)
        self._cursor.execute("CREATE INDEX IF NOT EXISTS idx_nodes_file_id ON nodes (file_id)")
        self._cursor.execute("CREATE INDEX IF NOT EXISTS idx_nodes_spatial ON nodes (file_path, byte_start)")

        # --- 3. TABELLE GRAFO E CONTENUTI ---
        self._cursor.execute("CREATE TABLE IF NOT EXISTS contents (chunk_hash TEXT PRIMARY KEY, content TEXT)")
        
        self._cursor.execute("""
            CREATE TABLE IF NOT EXISTS edges (
                source_id TEXT, target_id TEXT, relation_type TEXT, metadata_json TEXT
            )
        """)
        self._cursor.execute("CREATE INDEX IF NOT EXISTS idx_edges_source ON edges (source_id)") 
        self._cursor.execute("CREATE INDEX IF NOT EXISTS idx_edges_target ON edges (target_id)")

        # --- 4. TABELLE RICERCA (FTS & Vectors) ---
        try:
            self._cursor.execute("CREATE VIRTUAL TABLE IF NOT EXISTS contents_fts USING fts5(chunk_hash, content, tokenize='trigram')")
        except Exception:
            self._cursor.execute("CREATE VIRTUAL TABLE IF NOT EXISTS contents_fts USING fts5(chunk_hash, content)")

        # [REMOVED] Colonna chunk_type anche da qui per coerenza
        self._cursor.execute("""
            CREATE TABLE IF NOT EXISTS node_embeddings (
                id TEXT PRIMARY KEY,          
                chunk_id TEXT,                
                repo_id TEXT,                 
                file_path TEXT,
                directory TEXT,               
                branch TEXT,                  
                language TEXT,
                category TEXT,                
                start_line INTEGER,
                end_line INTEGER,
                text_content TEXT,            
                vector_hash TEXT,             
                model_name TEXT,              
                created_at TEXT,
                embedding BLOB                
            )
        """)
        self._cursor.execute("CREATE INDEX IF NOT EXISTS idx_emb_hash ON node_embeddings (vector_hash)")
        self._cursor.execute("CREATE INDEX IF NOT EXISTS idx_emb_repo ON node_embeddings (repo_id)")
        
        self._conn.commit()

    # ==========================================
    # REPOSITORY MANAGEMENT
    # ==========================================

    def get_repository(self, repo_id: str) -> Optional[Dict[str, Any]]:
        self._cursor.execute("SELECT * FROM repositories WHERE id = ?", (repo_id,))
        row = self._cursor.fetchone()
        if not row: return None
        cols = [d[0] for d in self._cursor.description]
        return dict(zip(cols, row))

    def get_repository_by_context(self, url: str, branch: str) -> Optional[Dict[str, Any]]:
        self._cursor.execute("SELECT * FROM repositories WHERE url = ? AND branch = ?", (url, branch))
        row = self._cursor.fetchone()
        if not row: return None
        cols = [d[0] for d in self._cursor.description]
        return dict(zip(cols, row))

    def register_repository(self, id: str, name: str, url: str, branch: str, commit_hash: str, local_path: str = None) -> str:
        now = datetime.datetime.utcnow().isoformat()
        existing = self.get_repository_by_context(url, branch)
        
        if existing:
            repo_id = existing['id']
            self._cursor.execute("""
                UPDATE repositories 
                SET name=?, last_commit=?, status='indexing', updated_at=?, local_path=?
                WHERE id=?
            """, (name, commit_hash, now, local_path, repo_id))
        else:
            repo_id = str(uuid.uuid4())
            self._cursor.execute("""
                INSERT INTO repositories (id, name, url, branch, last_commit, status, updated_at, local_path)
                VALUES (?, ?, ?, ?, ?, 'indexing', ?, ?)
            """, (repo_id, name, url, branch, commit_hash, now, local_path))
            
        self._conn.commit()
        return repo_id

    def update_repository_status(self, repo_id: str, status: str, commit_hash: str = None):
        now = datetime.datetime.utcnow().isoformat()
        if commit_hash:
            self._cursor.execute("UPDATE repositories SET status = ?, last_commit = ?, updated_at = ? WHERE id = ?", 
                               (status, commit_hash, now, repo_id))
        else:
            self._cursor.execute("UPDATE repositories SET status = ?, updated_at = ? WHERE id = ?", 
                               (status, now, repo_id))
        self._conn.commit()

    def delete_previous_data(self, repo_id: str, branch: str):
        try:
            self._cursor.execute("DELETE FROM node_embeddings WHERE repo_id = ?", (repo_id,))
            
            # Cascata: Edges -> Nodes -> Files
            self._cursor.execute("""
                DELETE FROM edges WHERE source_id IN (
                    SELECT n.id FROM nodes n JOIN files f ON n.file_id = f.id WHERE f.repo_id = ?
                )
            """, (repo_id,))

            self._cursor.execute("""
                DELETE FROM nodes WHERE file_id IN (SELECT id FROM files WHERE repo_id = ?)
            """, (repo_id,))

            self._cursor.execute("DELETE FROM files WHERE repo_id = ?", (repo_id,))
            self._conn.commit()
        except Exception as e:
            logger.error(f"Errore delete_previous_data: {e}")

    # ==========================================
    # WRITE METHODS
    # ==========================================

    def add_files(self, files: List[Any]):
        sql_batch = []
        for f in files:
            d = f.to_dict() if hasattr(f, 'to_dict') else f
            sql_batch.append((
                d['id'], d['repo_id'], d.get('commit_hash', ''), d['file_hash'],
                d['path'], d['language'], d['size_bytes'], 
                d['category'], d['indexed_at']
            ))
        if sql_batch:
            self._cursor.executemany("INSERT OR REPLACE INTO files VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", sql_batch)
            self._conn.commit()

    def add_nodes(self, nodes: List[Any]):
        sql_batch = []
        for n in nodes:
            d = n.to_dict() if hasattr(n, 'to_dict') else n
            b_start = d['byte_range'][0]
            b_end = d['byte_range'][1]
            meta = json.dumps(d.get('metadata', {}))
            
            # [FIX] type rimosso dalla query
            sql_batch.append((
                d['id'], 
                d.get('file_id'),
                d['file_path'], 
                d['start_line'], d['end_line'],
                b_start, b_end, d.get('chunk_hash', ''),
                b_end - b_start,
                meta
            ))
        if sql_batch:
            self._cursor.executemany("INSERT OR IGNORE INTO nodes VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", sql_batch)
            self._conn.commit()

    def add_contents(self, contents: List[Any]):
        sql_batch = []
        fts_batch = []
        for c in contents:
            d = c.to_dict() if hasattr(c, 'to_dict') else c
            sql_batch.append((d['chunk_hash'], d['content']))
            fts_batch.append((d['chunk_hash'], d['content']))
        if sql_batch:
            self._cursor.executemany("INSERT OR IGNORE INTO contents VALUES (?, ?)", sql_batch)
            try:
                self._cursor.executemany("INSERT INTO contents_fts (chunk_hash, content) VALUES (?, ?)", fts_batch)
            except: pass 
            self._conn.commit()

    def add_edge(self, source_id: str, target_id: str, relation_type: str, metadata: Dict[str, Any]):
        self._cursor.execute(
            "INSERT INTO edges VALUES (?, ?, ?, ?)", 
            (source_id, target_id, relation_type, json.dumps(metadata))
        )

    def save_embeddings(self, vector_documents: List[Dict[str, Any]]):
        sql_batch = []
        for doc in vector_documents:
            vector = doc["vector"]
            vector_blob = struct.pack(f'{len(vector)}f', *vector)
            
            # [FIX] chunk_type rimosso
            sql_batch.append((
                doc['id'], doc['chunk_id'], doc.get('repo_id'), doc.get('file_path'),
                doc.get('directory'), doc.get('branch'), doc.get('language'),
                doc.get('category'), # chunk_type removed
                doc.get('start_line'), doc.get('end_line'), 
                doc.get('text_content'), doc.get('vector_hash'),
                doc.get('model_name'), doc.get('created_at'), vector_blob
            ))
        if sql_batch:
            p = ",".join(["?"] * 15) # 15 colonne ora
            self._cursor.executemany(f"INSERT OR REPLACE INTO node_embeddings VALUES ({p})", sql_batch)
            self._conn.commit()

    # ==========================================
    # RETRIEVAL METHODS
    # ==========================================

    def search_fts(self, query: str, limit: int = 20, repo_id: str = None, branch: str = None) -> List[Dict[str, Any]]:
        """
        Keyword Search con strategie di Fallback.
        """
        clean_query = query.replace('"', '').replace("'", "")
        words = clean_query.split()
        if not words: return []

        # Strategie: Phrase -> AND -> OR
        strategies = [f'"{clean_query}"']
        if len(words) > 1:
            strategies.append(" AND ".join(words))
            strategies.append(" OR ".join(words))

        base_sql = """
            SELECT 
                n.id, n.file_path, n.start_line, n.end_line, 
                contents_fts.rank, c.content, f.repo_id, r.branch, n.metadata_json
            FROM contents_fts
            JOIN nodes n ON n.chunk_hash = contents_fts.chunk_hash
            JOIN contents c ON c.chunk_hash = n.chunk_hash
            JOIN files f ON n.file_id = f.id
            JOIN repositories r ON f.repo_id = r.id
            WHERE contents_fts MATCH ? 
        """
        
        params_base = []
        if repo_id:
            base_sql += " AND f.repo_id = ?"
            params_base.append(repo_id)
        if branch:
            base_sql += " AND r.branch = ?"
            params_base.append(branch)
            
        base_sql += " ORDER BY contents_fts.rank ASC LIMIT ?"
        params_base.append(limit)

        for strategy_query in strategies:
            try:
                self._cursor.execute(base_sql, [strategy_query] + params_base)
                rows = self._cursor.fetchall()
                if rows:
                    results = []
                    for row in rows:
                        results.append({
                            "id": row[0],
                            "file_path": row[1],
                            "start_line": row[2],
                            "end_line": row[3],
                            "score": row[4],
                            "content": row[5],
                            "repo_id": row[6],
                            "branch": row[7],
                            "metadata": json.loads(row[8] or "{}") # [NEW] Metadata recuperato qui
                        })
                    return results
            except sqlite3.OperationalError:
                continue # Prova la prossima strategia
        return []

    def search_vectors(self, query_vector: List[float], limit: int = 20, repo_id: str = None, branch: str = None) -> List[Dict[str, Any]]:
        if not HAS_NUMPY: return []

        # [FIX] JOIN con nodes per avere i metadati freschi e corretti
        sql = """
            SELECT ne.id, ne.embedding, ne.chunk_id, ne.file_path, 
                   ne.text_content, ne.start_line, ne.end_line, 
                   ne.repo_id, ne.branch, n.metadata_json
            FROM node_embeddings ne
            JOIN nodes n ON ne.chunk_id = n.id
            WHERE 1=1
        """
        params = []
        
        if repo_id:
            sql += " AND ne.repo_id = ?"
            params.append(repo_id)
        
        if branch:
            sql += " AND ne.branch = ?"
            params.append(branch)
            
        self._cursor.execute(sql, params)
        rows = self._cursor.fetchall()
        
        if not rows: return []

        ids = []
        vectors = []
        metadata_map = {}
        
        dim = len(query_vector)
        fmt = f"{dim}f"
        
        for r in rows:
            emb_id = r[0]
            blob = r[1]
            if not blob or len(blob) != dim * 4: continue 
            
            try:
                vec = struct.unpack(fmt, blob)
                vectors.append(vec)
                ids.append(emb_id)
                
                metadata_map[emb_id] = {
                    "id": r[2],
                    "file_path": r[3],
                    "content": r[4],
                    "start_line": r[5],
                    "end_line": r[6],
                    "repo_id": r[7],
                    "branch": r[8],
                    "metadata": json.loads(r[9] or "{}") # [NEW] Metadata dal join
                }
            except Exception: continue

        if not vectors: return []

        np_vecs = np.array(vectors, dtype=np.float32)
        np_query = np.array(query_vector, dtype=np.float32)
        
        norm_vecs = np.linalg.norm(np_vecs, axis=1, keepdims=True)
        norm_query = np.linalg.norm(np_query)
        
        if norm_query == 0: return []
        norm_vecs[norm_vecs == 0] = 1e-10
        
        similarities = np.dot(np_vecs, np_query) / (norm_vecs.squeeze() * norm_query)
        
        k_indices = np.argsort(similarities)[-limit:][::-1]
        
        results = []
        for idx in k_indices:
            emb_id = ids[idx]
            score = float(similarities[idx])
            meta = metadata_map[emb_id]
            results.append({**meta, "score": score})
            
        return results

    # ==========================================
    # NAVIGATOR METHODS
    # ==========================================

    def get_context_neighbors(self, node_id: str) -> Dict[str, List[Dict[str, Any]]]:
        result = {"parents": [], "calls": []}
        
        # 1. Parents (child_of) - [FIX] removed type field
        self._cursor.execute("""
            SELECT t.id, t.file_path, t.start_line, e.metadata_json, t.metadata_json
            FROM edges e
            JOIN nodes t ON e.target_id = t.id
            WHERE e.source_id = ? AND e.relation_type = 'child_of'
        """, (node_id,))
        for row in self._cursor:
            node_meta = json.loads(row[4] or "{}")
            result["parents"].append({
                "id": row[0], 
                "file_path": row[1], 
                "start_line": row[2], 
                "edge_meta": json.loads(row[3] or "{}"),
                "metadata": node_meta # Per estrarre semantic_labels dopo
            })

        # 2. Calls
        self._cursor.execute("""
            SELECT t.id, t.file_path, e.metadata_json
            FROM edges e
            JOIN nodes t ON e.target_id = t.id
            WHERE e.source_id = ? AND e.relation_type IN ('calls', 'references')
            LIMIT 15
        """, (node_id,))
        for row in self._cursor:
            meta = json.loads(row[2] or "{}")
            result["calls"].append({
                "id": row[0], "symbol": meta.get("symbol", "unknown")
            })
            
        return result

    def get_neighbor_chunk(self, node_id: str, direction: str = "next") -> Optional[Dict[str, Any]]:
        self._cursor.execute("SELECT file_id, start_line, end_line FROM nodes WHERE id = ?", (node_id,))
        curr = self._cursor.fetchone()
        if not curr: return None
        
        file_id, start, end = curr
        
        # [FIX] removed type from select, added metadata_json
        if direction == "next":
            sql = """
                SELECT n.id, n.start_line, n.end_line, n.chunk_hash, c.content, n.metadata_json
                FROM nodes n
                JOIN contents c ON n.chunk_hash = c.chunk_hash
                WHERE n.file_id = ? AND n.start_line >= ? AND n.id != ?
                ORDER BY n.start_line ASC LIMIT 1
            """
            params = (file_id, end, node_id)
        else:
            sql = """
                SELECT n.id, n.start_line, n.end_line, n.chunk_hash, c.content, n.metadata_json
                FROM nodes n
                JOIN contents c ON n.chunk_hash = c.chunk_hash
                WHERE n.file_id = ? AND n.end_line <= ? AND n.id != ?
                ORDER BY n.end_line DESC LIMIT 1
            """
            params = (file_id, start, node_id)

        self._cursor.execute(sql, params)
        row = self._cursor.fetchone()
        
        if row:
            return {
                "id": row[0], "start_line": row[1], "end_line": row[2], 
                "chunk_hash": row[3], "content": row[4],
                "metadata": json.loads(row[5] or "{}")
            }
        return None

    def get_incoming_references(self, target_node_id: str, limit: int = 50) -> List[Dict[str, Any]]:
        # [FIX] removed source_type
        self._cursor.execute("""
            SELECT s.id, s.file_path, s.start_line, e.relation_type, e.metadata_json
            FROM edges e
            JOIN nodes s ON e.source_id = s.id
            WHERE e.target_id = ? 
            AND e.relation_type IN ('calls', 'references', 'imports', 'instantiates')
            ORDER BY s.file_path, s.start_line
            LIMIT ?
        """, (target_node_id, limit))
        results = []
        for row in self._cursor:
            meta = json.loads(row[4] or "{}")
            results.append({
                "source_id": row[0],
                "file": row[1],
                "line": row[2],
                "relation": row[3],
                "context_snippet": meta.get("description", "")
            })
        return results

    def get_outgoing_calls(self, source_node_id: str, limit: int = 50) -> List[Dict[str, Any]]:
        # [FIX] removed target_type
        self._cursor.execute("""
            SELECT t.id, t.file_path, t.start_line, e.relation_type, e.metadata_json
            FROM edges e
            JOIN nodes t ON e.target_id = t.id
            WHERE e.source_id = ?
            AND e.relation_type IN ('calls', 'instantiates', 'imports')
            ORDER BY t.file_path, t.start_line
            LIMIT ?
        """, (source_node_id, limit))
        results = []
        for row in self._cursor:
            meta = json.loads(row[4] or "{}")
            results.append({
                "target_id": row[0],
                "file": row[1],
                "line": row[2],
                "relation": row[3],
                "symbol": meta.get("symbol", "")
            })
        return results

    # ==========================================
    # BATCH & UTILS
    # ==========================================

    def get_nodes_cursor(self, repo_id: str = None, branch: str = None) -> Generator[Dict[str, Any], None, None]:
        # [FIX] removed type from select
        base_query = """
            SELECT n.id, n.file_path, n.chunk_hash, n.start_line, n.end_line, 
                   f.repo_id, f.language, f.category
            FROM nodes n
            JOIN files f ON n.file_id = f.id 
            JOIN repositories r ON f.repo_id = r.id 
            WHERE 1=1
        """
        
        params = []
        if repo_id:
            base_query += " AND f.repo_id = ?"
            params.append(repo_id)
            
        cursor = self._conn.cursor()
        cursor.execute(base_query, params)
        if cursor.description:
            cols = [d[0] for d in cursor.description]
            for row in cursor:
                yield dict(zip(cols, row))
        cursor.close()

    def get_nodes_to_embed(self, repo_id: str, model_name: str) -> Generator[Dict[str, Any], None, None]:
        # [FIX] removed type from select
        sql = """
            SELECT 
                n.id, n.file_path, n.chunk_hash, n.start_line, n.end_line, 
                f.repo_id, r.branch, f.language, f.category 
            FROM files f
            JOIN repositories r ON f.repo_id = r.id
            JOIN nodes n ON f.id = n.file_id
            LEFT JOIN node_embeddings ne ON (
                n.id = ne.chunk_id 
                AND ne.model_name = ?
            )
            WHERE f.repo_id = ? 
              AND ne.id IS NULL
        """
        
        c = self._conn.cursor()
        c.execute(sql, (model_name, repo_id))
        
        if c.description:
            cols = [d[0] for d in c.description]
            for row in c:
                yield dict(zip(cols, row))
        c.close()

    def find_chunk_id(self, file_path: str, byte_range: List[int], repo_id: str = None) -> Optional[str]:
        if not byte_range: return None
        sql = """
            SELECT n.id 
            FROM nodes n
            JOIN files f ON n.file_id = f.id
            WHERE f.path = ? 
              AND n.byte_start <= ? + 1 AND n.byte_end >= ? - 1
        """
        params = [file_path, byte_range[0], byte_range[1]]
        
        if repo_id:
            sql += " AND f.repo_id = ?"
            params.append(repo_id)
            
        sql += " ORDER BY n.size ASC LIMIT 1"
        
        self._cursor.execute(sql, params)
        row = self._cursor.fetchone()
        return row[0] if row else None

    def ensure_external_node(self, node_id: str):
        # I nodi esterni non hanno file_id né type
        try: self._cursor.execute("INSERT OR IGNORE INTO nodes (id) VALUES (?)", (node_id,))
        except: pass

    # Metodi di Batch Read (get_files_bulk, etc)
    def get_files_bulk(self, file_paths: List[str], repo_id: str = None) -> Dict[str, Dict[str, Any]]:
        if not file_paths: return {}
        unique_paths = list(set(file_paths))
        result = {}
        
        for i in range(0, len(unique_paths), 900):
            batch = unique_paths[i:i+900]
            ph = ",".join(["?"] * len(batch))
            
            sql = f"SELECT path, repo_id, language, category FROM files WHERE path IN ({ph})"
            params = list(batch)
            
            if repo_id:
                sql += " AND repo_id = ?"
                params.append(repo_id)
            
            self._cursor.execute(sql, params)
            if self._cursor.description:
                cols = [d[0] for d in self._cursor.description]
                for row in self._cursor:
                    result[row[0]] = dict(zip(cols, row))
        return result

    def get_contents_bulk(self, chunk_hashes: List[str]) -> Dict[str, str]:
        if not chunk_hashes: return {}
        result = {}
        for i in range(0, len(chunk_hashes), 900):
            batch = chunk_hashes[i:i+900]
            ph = ",".join(["?"] * len(batch))
            self._cursor.execute(f"SELECT chunk_hash, content FROM contents WHERE chunk_hash IN ({ph})", batch)
            for row in self._cursor:
                result[row[0]] = row[1]
        return result

    def get_incoming_definitions_bulk(self, node_ids: List[str]) -> Dict[str, List[str]]:
        if not node_ids: return {}
        result = {} 
        for i in range(0, len(node_ids), 900):
            batch = node_ids[i:i+900]
            ph = ",".join(["?"] * len(batch))
            self._cursor.execute(f"""
                SELECT target_id, metadata_json 
                FROM edges 
                WHERE target_id IN ({ph}) 
                AND relation_type = 'calls'
            """, batch)
            
            for row in self._cursor:
                tid, meta_json = row
                if not meta_json: continue
                try:
                    sym = json.loads(meta_json).get("symbol")
                    if sym:
                        if tid not in result: result[tid] = set()
                        result[tid].add(sym)
                except: pass
        return {k: list(v) for k, v in result.items()}

    def get_stats(self):
        self._conn.commit()
        return {
            "files": self._cursor.execute("SELECT COUNT(*) FROM files").fetchone()[0],
            "total_nodes": self._cursor.execute("SELECT COUNT(*) FROM nodes").fetchone()[0],
            "embeddings": self._cursor.execute("SELECT COUNT(*) FROM node_embeddings").fetchone()[0],
            "repositories": self._cursor.execute("SELECT COUNT(*) FROM repositories").fetchone()[0]
        }
    
    def commit(self): self._conn.commit()
    
    def close(self): 
        try: self._conn.close()
        except: pass

    # Stub per interfaccia base (se richiesti)
    def get_all_files(self): yield from []
    def get_all_nodes(self): yield from []
    def get_all_contents(self): yield from []
    def get_all_edges(self): yield from []