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

        # --- TABELLA REPOSITORIES ---
        self._cursor.execute("""
            CREATE TABLE IF NOT EXISTS repositories (
                id TEXT PRIMARY KEY,        -- UUID Univoco per questa istanza (Repo + Branch)
                url TEXT NOT NULL,
                branch TEXT NOT NULL,
                name TEXT,
                last_commit TEXT,
                status TEXT,                -- 'indexing', 'completed', 'failed'
                updated_at TEXT,
                local_path TEXT,            -- Path fisico del worktree
                UNIQUE(url, branch)         -- Vincolo fondamentale per il multi-branch
            )
        """)

        # --- TABELLE BASE ---
        self._cursor.execute("""
            CREATE TABLE IF NOT EXISTS files (
                id TEXT PRIMARY KEY, 
                repo_id TEXT,               -- FK su repositories.id
                commit_hash TEXT, 
                file_hash TEXT,
                path TEXT, 
                language TEXT, 
                size_bytes INTEGER, 
                category TEXT, 
                indexed_at TEXT,
                UNIQUE(repo_id, path)       -- Ogni file è univoco nel contesto del suo branch (repo_id)
            )
        """)
        self._cursor.execute("CREATE INDEX IF NOT EXISTS idx_files_path ON files (path)")
        self._cursor.execute("CREATE INDEX IF NOT EXISTS idx_files_repo ON files (repo_id)")

        # [FIX] Aggiunto file_id per linking robusto
        self._cursor.execute("""
            CREATE TABLE IF NOT EXISTS nodes (
                id TEXT PRIMARY KEY, 
                file_id TEXT,               -- FK su files.id (IL COLLEGAMENTO FORTE)
                type TEXT, 
                file_path TEXT,             -- Ridondante ma utile per query veloci senza join
                start_line INTEGER, end_line INTEGER, byte_start INTEGER, byte_end INTEGER,
                chunk_hash TEXT, 
                size INTEGER,
                metadata_json TEXT 
            )
        """)
        self._cursor.execute("CREATE INDEX IF NOT EXISTS idx_nodes_file_id ON nodes (file_id)")
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


    # --- DELETE PREVIOUS DATA ---
    def delete_previous_data(self, repo_id: str, branch: str):
        try:
            # 1. Pulizia Embeddings
            self._cursor.execute("DELETE FROM node_embeddings WHERE repo_id = ?", (repo_id,))
            
            # 2. Pulizia Archi (Edges) - Cascata tramite file
            self._cursor.execute("""
                DELETE FROM edges 
                WHERE source_id IN (
                    SELECT n.id 
                    FROM nodes n
                    JOIN files f ON n.file_id = f.id 
                    WHERE f.repo_id = ?
                )
            """, (repo_id,))

            # 3. Pulizia Nodi - Cascata tramite file
            self._cursor.execute("""
                DELETE FROM nodes 
                WHERE file_id IN (
                    SELECT id FROM files WHERE repo_id = ?
                )
            """, (repo_id,))

            # 4. Pulizia Files
            self._cursor.execute("DELETE FROM files WHERE repo_id = ?", (repo_id,))
            
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
            self._cursor.executemany("INSERT OR REPLACE INTO files VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", sql_batch)
            self._conn.commit()

    def add_nodes(self, nodes: List[Any]):
        sql_batch = []
        for n in nodes:
            d = n.to_dict() if hasattr(n, 'to_dict') else n
            b_start = d['byte_range'][0]
            b_end = d['byte_range'][1]
            meta = json.dumps(d.get('metadata', {}))
            
            # [FIX] Inseriamo anche file_id
            sql_batch.append((
                d['id'], 
                d.get('file_id'),  # <--- NEW: Link forte al file
                d['type'], d['file_path'], 
                d['start_line'], d['end_line'],
                b_start, b_end, d.get('chunk_hash', ''),
                b_end - b_start,
                meta
            ))
        if sql_batch:
            # Aggiornata query con 11 parametri invece di 10
            self._cursor.executemany("INSERT OR IGNORE INTO nodes VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", sql_batch)
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
        
        # [FIX] JOIN robusta su file_id
        base_query = """
            SELECT n.id, n.type, n.file_path, n.chunk_hash, n.start_line, n.end_line, 
                   f.repo_id, f.language, f.category
            FROM nodes n
            JOIN files f ON n.file_id = f.id 
            JOIN repositories r ON f.repo_id = r.id 
            WHERE n.type NOT IN ('external_library', 'program', 'module')
        """
        
        params = []
        if repo_id:
            base_query += " AND f.repo_id = ?"
            params.append(repo_id)
            
        iter_cursor.execute(base_query, params)
        if iter_cursor.description:
            cols = [d[0] for d in iter_cursor.description]
            for row in iter_cursor:
                yield dict(zip(cols, row))
        iter_cursor.close()

    def get_nodes_to_embed(self, repo_id: str, model_name: str) -> Generator[Dict[str, Any], None, None]:
        """
        Restituisce SOLO i nodi di una repository che non hanno ancora un embedding
        per il modello specificato. Ottimizzazione incrementale.
        """
        # [FIX] JOIN robusta su file_id per evitare cross-repo leak
        sql = """
            SELECT 
                n.id, n.type, n.file_path, n.chunk_hash, n.start_line, n.end_line, 
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
              AND n.type NOT IN ('external_library', 'program', 'module')
        """
        
        c = self._conn.cursor()
        c.execute(sql, (model_name, repo_id))
        
        if c.description:
            cols = [d[0] for d in c.description]
            for row in c:
                yield dict(zip(cols, row))
        c.close()

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

    def get_files_bulk(self, file_paths: List[str], repo_id: str = None) -> Dict[str, Dict[str, Any]]:
        if not file_paths: return {}
        unique_paths = list(set(file_paths))
        BATCH_SIZE = 900
        result = {}
        
        for i in range(0, len(unique_paths), BATCH_SIZE):
            batch = unique_paths[i:i+BATCH_SIZE]
            placeholders = ",".join(["?"] * len(batch))
            
            # Query base
            query = f"SELECT path, repo_id, language, category FROM files WHERE path IN ({placeholders})"
            params = list(batch)
            
            if repo_id:
                query += " AND repo_id = ?"
                params.append(repo_id)
            
            self._cursor.execute(query, params)
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
    def find_chunk_id(self, file_path: str, byte_range: List[int], repo_id: str = None) -> Optional[str]:
        if not byte_range: return None
        
        # JOIN con la tabella files per verificare il repo_id
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

    # --- IMPLEMENTAZIONE RETRIEVAL ---

    def search_fts(self, query: str, limit: int = 20, repo_id: str = None, branch: str = None) -> List[Dict[str, Any]]:
        """
        Esegue Keyword Search con strategia di fallback:
        1. Phrase Match (Esatto)
        2. AND Match (Tutte le parole presenti)
        3. OR Match (Almeno una parola presente)
        """
        # Pulizia base della query
        clean_query = query.replace('"', '').replace("'", "")
        words = clean_query.split()
        
        # Se la query è vuota, esci
        if not words: return []

        # Costruiamo 3 strategie di query FTS5
        strategies = []
        
        # 1. PHRASE MATCH: "parola1 parola2" (Deve esistere la frase esatta)
        strategies.append(f'"{clean_query}"')
        
        # 2. AND MATCH: parola1 AND parola2 (Devono esserci tutte, ordine sparso)
        if len(words) > 1:
            strategies.append(" AND ".join(words))
            
        # 3. OR MATCH: parola1 OR parola2 (Basta che ce ne sia una - Relaxed)
        if len(words) > 1:
            strategies.append(" OR ".join(words))

        # Query SQL Base (uguale a prima)
        base_sql = """
            SELECT 
                n.id, n.file_path, n.start_line, n.end_line, n.type,
                contents_fts.rank,
                c.content,
                f.repo_id,
                r.branch
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

        # --- ESECUZIONE FALLBACK ---
        for strategy_query in strategies:
            try:
                # Copiamo i parametri base e inseriamo la query corrente all'inizio
                current_params = [strategy_query] + params_base
                
                self._cursor.execute(base_sql, current_params)
                rows = self._cursor.fetchall()
                
                # Se troviamo risultati con questa strategia, li restituiamo e ci fermiamo!
                # Questo privilegia i match esatti rispetto a quelli "sporchi".
                if rows:
                    results = []
                    for row in rows:
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
                # Se una strategia fallisce (es. caratteri illegali), proviamo la successiva
                logger.warning(f"FTS Strategy '{strategy_query}' failed: {e}")
                continue

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
    
    def get_context_neighbors(self, node_id: str) -> Dict[str, List[Dict[str, Any]]]:
        result = {"parents": [], "calls": []}
        
        # 1. Parents (Vertical)
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

        # 2. Calls (Horizontal - Sampling)
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

    def get_neighbor_chunk(self, node_id: str, direction: str = "next") -> Optional[Dict[str, Any]]:
        # Recupera file_id e posizione del nodo corrente
        self._cursor.execute("SELECT file_id, start_line, end_line FROM nodes WHERE id = ?", (node_id,))
        curr = self._cursor.fetchone()
        if not curr: return None
        
        file_id, start, end = curr
        
        if direction == "next":
            # Cerca il primo nodo nello stesso file che inizia dopo o alla fine di questo
            sql = """
                SELECT n.id, n.type, n.start_line, n.end_line, n.chunk_hash, c.content
                FROM nodes n
                LEFT JOIN contents c ON n.chunk_hash = c.chunk_hash
                WHERE n.file_id = ? AND n.start_line >= ? AND n.id != ?
                ORDER BY n.start_line ASC, n.byte_start ASC
                LIMIT 1
            """
            params = (file_id, end, node_id) # Cerca dopo la fine del corrente (o sovrapposti dopo)
        else:
            # Prev
            sql = """
                SELECT n.id, n.type, n.start_line, n.end_line, n.chunk_hash, c.content
                FROM nodes n
                LEFT JOIN contents c ON n.chunk_hash = c.chunk_hash
                WHERE n.file_id = ? AND n.end_line <= ? AND n.id != ?
                ORDER BY n.end_line DESC, n.byte_start DESC
                LIMIT 1
            """
            params = (file_id, start, node_id) # Cerca prima dell'inizio

        self._cursor.execute(sql, params)
        row = self._cursor.fetchone()
        
        if row:
            return {
                "id": row[0], "type": row[1], "start_line": row[2], 
                "end_line": row[3], "chunk_hash": row[4], "content": row[5]
            }
        return None

    def get_incoming_references(self, target_node_id: str, limit: int = 50) -> List[Dict[str, Any]]:
        """
        Chi usa questo nodo? (Reverse dependencies)
        """
        sql = """
            SELECT 
                s.id, s.type, s.file_path, s.start_line, 
                e.relation_type, e.metadata_json
            FROM edges e
            JOIN nodes s ON e.source_id = s.id
            WHERE e.target_id = ? 
            AND e.relation_type IN ('calls', 'references', 'imports', 'instantiates')
            ORDER BY s.file_path, s.start_line
            LIMIT ?
        """
        self._cursor.execute(sql, (target_node_id, limit))
        results = []
        for row in self._cursor:
            meta = json.loads(row[5] or "{}")
            # Costruiamo uno snippet di contesto
            results.append({
                "source_id": row[0],
                "source_type": row[1],
                "file": row[2],
                "line": row[3],
                "relation": row[4],
                "context_snippet": meta.get("description", "")
            })
        return results

    def get_outgoing_calls(self, source_node_id: str, limit: int = 50) -> List[Dict[str, Any]]:
        """
        Cosa chiama questo nodo? (Forward dependencies)
        """
        sql = """
            SELECT 
                t.id, t.type, t.file_path, t.start_line,
                e.relation_type, e.metadata_json
            FROM edges e
            JOIN nodes t ON e.target_id = t.id
            WHERE e.source_id = ?
            AND e.relation_type IN ('calls', 'instantiates', 'imports')
            ORDER BY t.file_path, t.start_line
            LIMIT ?
        """
        self._cursor.execute(sql, (source_node_id, limit))
        results = []
        for row in self._cursor:
            meta = json.loads(row[5] or "{}")
            results.append({
                "target_id": row[0],
                "target_type": row[1],
                "file": row[2],
                "line": row[3],
                "relation": row[4],
                "symbol": meta.get("symbol", "")
            })
        return results