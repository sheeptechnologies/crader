import sqlite3
import os
import json
import logging
import struct
import datetime
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
        Inizializza lo storage.
        :param db_path: Percorso del file DB. Se None, usa un file predefinito locale.
        """
        self._db_file = os.path.abspath(db_path)
        logger.info(f"💾 Storage Database: {self._db_file}")
        
        self._conn = sqlite3.connect(self._db_file, check_same_thread=False)
        self._cursor = self._conn.cursor()
        
        self._cursor.execute("PRAGMA synchronous = OFF")
        self._cursor.execute("PRAGMA journal_mode = WAL")
        self._cursor.execute("PRAGMA cache_size = 5000")

        # --- TABELLA REPOSITORIES (Fase 1) ---
        self._cursor.execute("""
            CREATE TABLE IF NOT EXISTS repositories (
                id TEXT PRIMARY KEY,        -- Hash univoco dell'URL remoto
                url TEXT,
                name TEXT,
                branch TEXT,
                last_commit TEXT,
                status TEXT,                -- 'indexing', 'completed', 'failed'
                updated_at TEXT
            )
        """)

        # --- TABELLE BASE ---
        self._cursor.execute("""
            CREATE TABLE IF NOT EXISTS files (
                id TEXT PRIMARY KEY, repo_id TEXT, commit_hash TEXT, file_hash TEXT,
                path TEXT, language TEXT, size_bytes INTEGER, category TEXT, indexed_at TEXT
            )
        """)
        self._cursor.execute("CREATE INDEX IF NOT EXISTS idx_files_path ON files (path)")
        self._cursor.execute("CREATE INDEX IF NOT EXISTS idx_files_repo ON files (repo_id)")

        self._cursor.execute("""
            CREATE TABLE IF NOT EXISTS nodes (
                id TEXT PRIMARY KEY, 
                type TEXT, 
                file_path TEXT,
                start_line INTEGER, end_line INTEGER, byte_start INTEGER, byte_end INTEGER,
                chunk_hash TEXT, 
                size INTEGER,
                metadata_json TEXT 
            )
        """)
        self._cursor.execute("CREATE INDEX IF NOT EXISTS idx_nodes_spatial ON nodes (file_path, byte_start)")

        self._cursor.execute("CREATE TABLE IF NOT EXISTS contents (chunk_hash TEXT PRIMARY KEY, content TEXT)")
        
        self._cursor.execute("""
            CREATE TABLE IF NOT EXISTS edges (
                source_id TEXT, target_id TEXT, relation_type TEXT, metadata_json TEXT
            )
        """)
        self._cursor.execute("CREATE INDEX IF NOT EXISTS idx_edges_source ON edges (source_id)") 
        self._cursor.execute("CREATE INDEX IF NOT EXISTS idx_edges_target ON edges (target_id)")

        # --- TABELLE RICERCA ---
        try:
            self._cursor.execute("CREATE VIRTUAL TABLE IF NOT EXISTS contents_fts USING fts5(chunk_hash, content, tokenize='trigram')")
        except Exception:
            self._cursor.execute("CREATE VIRTUAL TABLE IF NOT EXISTS contents_fts USING fts5(chunk_hash, content)")

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
                chunk_type TEXT,              
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

    # --- REPOSITORY MANAGEMENT ---

    def get_repository(self, repo_id: str) -> Optional[Dict[str, Any]]:
        self._cursor.execute("SELECT * FROM repositories WHERE id = ?", (repo_id,))
        row = self._cursor.fetchone()
        if not row: return None
        cols = [d[0] for d in self._cursor.description]
        return dict(zip(cols, row))

    def register_repository(self, repo_id: str, name: str, url: str, branch: str, commit_hash: str):
        now = datetime.datetime.utcnow().isoformat()
        self._cursor.execute("""
            INSERT OR REPLACE INTO repositories (id, name, url, branch, last_commit, status, updated_at)
            VALUES (?, ?, ?, ?, ?, 'indexing', ?)
        """, (repo_id, name, url, branch, commit_hash, now))
        self._conn.commit()

    def update_repository_status(self, repo_id: str, status: str, commit_hash: str = None):
        now = datetime.datetime.utcnow().isoformat()
        if commit_hash:
            self._cursor.execute("UPDATE repositories SET status = ?, last_commit = ?, updated_at = ? WHERE id = ?", 
                               (status, commit_hash, now, repo_id))
        else:
            self._cursor.execute("UPDATE repositories SET status = ?, updated_at = ? WHERE id = ?", 
                               (status, now, repo_id))
        self._conn.commit()


    # --- DELETE PREVIOUS DATA ---
    def delete_previous_data(self, repo_id: str, branch: str):
        """
        Rimuove gli embedding obsoleti per questo branch per evitare duplicati nei risultati di ricerca.
        """
        try:
            # 1. Pulizia Embeddings (Hanno il campo branch esplicito)
            self._cursor.execute(
                "DELETE FROM node_embeddings WHERE repo_id = ? AND branch = ?", 
                (repo_id, branch)
            )
            count = self._cursor.rowcount
            if count > 0:
                logger.info(f"🧹 Puliti {count} embedding vecchi per {repo_id}/{branch}")
            
            # Nota: Non cancelliamo da 'nodes' o 'files' qui perché in questo schema SQLite
            # sono condivisi o mancano del campo branch. 
            # L'upsert di 'files' e 'nodes' gestirà gli aggiornamenti ID-based.
            
            self._conn.commit()
        except Exception as e:
            logger.error(f"Errore durante delete_previous_data: {e}")

    # --- WRITE METHODS ---

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
            self._cursor.executemany("INSERT OR IGNORE INTO files VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", sql_batch)
            self._conn.commit()

    def add_nodes(self, nodes: List[Any]):
        sql_batch = []
        for n in nodes:
            d = n.to_dict() if hasattr(n, 'to_dict') else n
            b_start = d['byte_range'][0]
            b_end = d['byte_range'][1]
            meta = json.dumps(d.get('metadata', {}))
            sql_batch.append((
                d['id'], d['type'], d['file_path'], 
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

    # --- BATCH OPTIMIZATION ---

    def get_nodes_cursor(self, repo_id: str = None, branch: str = None) -> Generator[Dict[str, Any], None, None]:
        iter_cursor = self._conn.cursor()
        
        base_query = """
            SELECT n.id, n.type, n.file_path, n.chunk_hash, n.start_line, n.end_line, 
                   f.repo_id, f.language, f.category
            FROM nodes n
            LEFT JOIN files f ON n.file_path = f.path 
            LEFT JOIN repositories r ON f.repo_id = r.id 
            WHERE n.type NOT IN ('external_library', 'program', 'module')
        """
        
        params = []
        if repo_id:
            base_query += " AND f.repo_id = ?"
            params.append(repo_id)
            
        if branch:
            base_query += " AND r.branch = ?"
            params.append(branch)
            
        iter_cursor.execute(base_query, params)
        if iter_cursor.description:
            cols = [d[0] for d in iter_cursor.description]
            for row in iter_cursor:
                yield dict(zip(cols, row))
        iter_cursor.close()

    def get_contents_bulk(self, chunk_hashes: List[str]) -> Dict[str, str]:
        if not chunk_hashes: return {}
        BATCH_SIZE = 900
        result = {}
        unique_hashes = list(set(chunk_hashes))

        for i in range(0, len(unique_hashes), BATCH_SIZE):
            batch = unique_hashes[i:i+BATCH_SIZE]
            placeholders = ",".join(["?"] * len(batch))
            query = f"SELECT chunk_hash, content FROM contents WHERE chunk_hash IN ({placeholders})"
            self._cursor.execute(query, batch)
            for row in self._cursor:
                result[row[0]] = row[1]
        return result

    def get_files_bulk(self, file_paths: List[str]) -> Dict[str, Dict[str, Any]]:
        if not file_paths: return {}
        unique_paths = list(set(file_paths))
        BATCH_SIZE = 900
        result = {}
        
        for i in range(0, len(unique_paths), BATCH_SIZE):
            batch = unique_paths[i:i+BATCH_SIZE]
            placeholders = ",".join(["?"] * len(batch))
            query = f"SELECT path, repo_id, language, category FROM files WHERE path IN ({placeholders})"
            self._cursor.execute(query, batch)
            if self._cursor.description:
                cols = [d[0] for d in self._cursor.description]
                for row in self._cursor:
                    result[row[0]] = dict(zip(cols, row))
        return result

    def get_incoming_definitions_bulk(self, node_ids: List[str]) -> Dict[str, List[str]]:
        if not node_ids: return {}
        unique_ids = list(set(node_ids))
        BATCH_SIZE = 900
        result = {} 
        
        for i in range(0, len(unique_ids), BATCH_SIZE):
            batch = unique_ids[i:i+BATCH_SIZE]
            placeholders = ",".join(["?"] * len(batch))
            query = f"""
                SELECT target_id, metadata_json 
                FROM edges 
                WHERE target_id IN ({placeholders}) 
                AND relation_type = 'calls'
            """
            self._cursor.execute(query, batch)
            
            for row in self._cursor:
                target_id, meta_json = row
                if not meta_json: continue
                try:
                    meta = json.loads(meta_json)
                    symbol = meta.get("symbol")
                    if symbol:
                        if target_id not in result: result[target_id] = set()
                        result[target_id].add(symbol)
                except: pass
        return {k: list(v) for k, v in result.items()}

    def save_embeddings(self, vector_documents: List[Dict[str, Any]]):
        sql_batch = []
        for doc in vector_documents:
            vector = doc["vector"]
            vector_blob = struct.pack(f'{len(vector)}f', *vector)
            
            sql_batch.append((
                doc['id'], doc['chunk_id'], doc.get('repo_id'), doc.get('file_path'),
                doc.get('directory'), doc.get('branch'), doc.get('language'),
                doc.get('category'), doc.get('chunk_type'), doc.get('start_line'),
                doc.get('end_line'), doc.get('text_content'), doc.get('vector_hash'),
                doc.get('model_name'), doc.get('created_at'), vector_blob
            ))
        if sql_batch:
            p = ",".join(["?"] * 16)
            self._cursor.executemany(f"INSERT OR REPLACE INTO node_embeddings VALUES ({p})", sql_batch)
            self._conn.commit()

    # --- READ HELPERS ---
    def find_chunk_id(self, file_path: str, byte_range: List[int]) -> Optional[str]:
        if not byte_range: return None
        self._cursor.execute(
            "SELECT id FROM nodes WHERE file_path = ? AND byte_start <= ? + 1 AND byte_end >= ? - 1 ORDER BY size ASC LIMIT 1",
            (file_path, byte_range[0], byte_range[1])
        )
        row = self._cursor.fetchone()
        return row[0] if row else None

    def ensure_external_node(self, node_id: str):
        try: self._cursor.execute("INSERT OR IGNORE INTO nodes (id, type) VALUES (?, ?)", (node_id, "external_library"))
        except: pass

    def get_stats(self):
        self._conn.commit()
        files = self._cursor.execute("SELECT COUNT(*) FROM files").fetchone()[0]
        nodes = self._cursor.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]
        embeddings = self._cursor.execute("SELECT COUNT(*) FROM node_embeddings").fetchone()[0]
        repos = self._cursor.execute("SELECT COUNT(*) FROM repositories").fetchone()[0]
        return {"files": files, "total_nodes": nodes, "embeddings": embeddings, "repositories": repos}
    
    def commit(self): self._conn.commit()
    
    def close(self): 
        try: self._conn.close()
        except: pass

    def get_all_files(self): 
        self._cursor.execute("SELECT * FROM files")
        if self._cursor.description:
            cols = [d[0] for d in self._cursor.description]
            for row in self._cursor: yield dict(zip(cols, row))
   
    def get_all_nodes(self): 
        self._cursor.execute("SELECT * FROM nodes")
        if self._cursor.description:
            cols = [d[0] for d in self._cursor.description]
            for row in self._cursor: yield dict(zip(cols, row))
   
    def get_all_contents(self): 
        self._cursor.execute("SELECT * FROM contents")
        if self._cursor.description:
            cols = [d[0] for d in self._cursor.description]
            for row in self._cursor: yield dict(zip(cols, row))
   
    def get_all_edges(self): 
        self._cursor.execute("SELECT * FROM edges")
        if self._cursor.description:
            cols = [d[0] for d in self._cursor.description]
            for row in self._cursor: yield dict(zip(cols, row))

    # --- IMPLEMENTAZIONE RETRIEVAL (FASE 2) ---

    def search_fts(self, query: str, limit: int = 20, repo_id: str = None, branch: str = None) -> List[Dict[str, Any]]:
        # 1. Raddoppiamo le virgolette esistenti nel testo (standard SQL/FTS escaping)
        escaped_query_text = query.replace('"', '""')
        
        # 2. Racchiudiamo TUTTO tra virgolette doppie.
        # Questo dice a FTS5: "Tratta tutto questo come una singola stringa letterale, 
        # non interpretare caratteri speciali come $, #, -, ecc."
        safe_query = f'"{escaped_query_text}"'
        
        # Se l'utente voleva usare la wildcard *, la ri-abilitiamo post-escaping se ha senso,
        # ma per la ricerca esatta di identificatori (Keyword Search) è meglio cercare il token preciso.
        # Se vuoi permettere ricerche parziali (es. "Legacy*"), la logica sarebbe più complessa.
        # Per ora, la priorità è non crashare su simboli strani.

        sql = """
            SELECT 
                n.id, n.file_path, n.start_line, n.end_line, n.type,
                contents_fts.rank,
                c.content,
                ne.repo_id,
                ne.branch
            FROM contents_fts
            JOIN nodes n ON n.chunk_hash = contents_fts.chunk_hash
            JOIN contents c ON c.chunk_hash = n.chunk_hash
            JOIN node_embeddings ne ON ne.chunk_id = n.id
            WHERE contents_fts MATCH ? 
        """
        params = [safe_query]
        
        if repo_id:
            sql += " AND ne.repo_id = ?"
            params.append(repo_id)
            
        if branch:
            sql += " AND ne.branch = ?"
            params.append(branch)
            
        sql += " ORDER BY contents_fts.rank ASC LIMIT ?"
        params.append(limit)
        
        try:
            self._cursor.execute(sql, params)
            results = []
            for row in self._cursor:
                results.append({
                    "id": row[0],
                    "file_path": row[1],
                    "start_line": row[2],
                    "end_line": row[3],
                    "type": row[4],
                    "score": row[5],
                    "content": row[6],
                    "repo_id": row[7],
                    "branch": row[8]
                })
            return results
        except sqlite3.OperationalError as e:
            # Ora questo dovrebbe accadere molto raramente
            logger.warning(f"FTS Search failed on query '{query}': {e}")
            return []

    def search_vectors(self, query_vector: List[float], limit: int = 20, repo_id: str = None, branch: str = None) -> List[Dict[str, Any]]:
        if not HAS_NUMPY: return []

        sql = """
            SELECT ne.id, ne.embedding, ne.chunk_id, ne.file_path, 
                   ne.chunk_type, ne.text_content, ne.start_line, ne.end_line, 
                   ne.repo_id, ne.branch
            FROM node_embeddings ne
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
                    "type": r[4],
                    "content": r[5],
                    "start_line": r[6],
                    "end_line": r[7],
                    "repo_id": r[8],
                    "branch": r[9]
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

    def get_context_neighbors(self, node_id: str) -> Dict[str, List[Dict[str, Any]]]:
        result = {"parents": [], "calls": []}
        
        sql_parent = """
            SELECT t.id, t.type, t.file_path, t.start_line, e.metadata_json
            FROM edges e
            JOIN nodes t ON e.target_id = t.id
            WHERE e.source_id = ? AND e.relation_type = 'child_of'
        """
        self._cursor.execute(sql_parent, (node_id,))
        for row in self._cursor:
            result["parents"].append({
                "id": row[0], "type": row[1], "file_path": row[2], 
                "start_line": row[3], "meta": json.loads(row[4] or "{}")
            })

        sql_calls = """
            SELECT t.id, t.type, t.file_path, e.metadata_json
            FROM edges e
            JOIN nodes t ON e.target_id = t.id
            WHERE e.source_id = ? AND (e.relation_type = 'calls' OR e.relation_type = 'references')
            LIMIT 15
        """
        self._cursor.execute(sql_calls, (node_id,))
        for row in self._cursor:
            meta = json.loads(row[3] or "{}")
            symbol = meta.get("symbol", "unknown")
            result["calls"].append({
                "id": row[0], "type": row[1], "symbol": symbol
            })
            
        return result