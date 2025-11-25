import sqlite3
import tempfile
import os
import json
import logging
from typing import List, Dict, Any, Optional, Generator
from .base import GraphStorage

logger = logging.getLogger(__name__)

class SqliteGraphStorage(GraphStorage):
    def __init__(self):
        self._db_file = tempfile.NamedTemporaryFile(delete=False, suffix=".db").name
        self._conn = sqlite3.connect(self._db_file)
        self._cursor = self._conn.cursor()
        
        self._cursor.execute("PRAGMA synchronous = OFF")
        self._cursor.execute("PRAGMA journal_mode = WAL")
        self._cursor.execute("PRAGMA cache_size = 5000")

        # 1. Tabella FILES
        self._cursor.execute("""
            CREATE TABLE files (
                id TEXT PRIMARY KEY,
                repo_id TEXT,
                commit_hash TEXT,
                file_hash TEXT,
                path TEXT,
                language TEXT,
                size_bytes INTEGER,
                category TEXT,
                indexed_at TEXT
            )
        """)
        self._cursor.execute("CREATE INDEX idx_filepath ON files (path)")

        # 2. Tabella NODI (Struttura)
        self._cursor.execute("""
            CREATE TABLE nodes (
                id TEXT PRIMARY KEY,
                type TEXT,
                file_path TEXT,
                start_line INTEGER,
                end_line INTEGER,
                byte_start INTEGER,
                byte_end INTEGER,
                chunk_hash TEXT,
                size INTEGER,
                metadata_json TEXT
            )
        """)
        self._cursor.execute("CREATE INDEX idx_spatial ON nodes (file_path, byte_start)")

        # 3. Tabella CONTENUTI (Dati)
        self._cursor.execute("""
            CREATE TABLE contents (
                chunk_hash TEXT PRIMARY KEY,
                content TEXT
            )
        """)
        
        # 4. Tabella ARCHI
        self._cursor.execute("""
            CREATE TABLE edges (
                source_id TEXT,
                target_id TEXT,
                relation_type TEXT,
                metadata_json TEXT
            )
        """)
        self._conn.commit()

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
            self._cursor.executemany("INSERT INTO nodes VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", sql_batch)
            self._conn.commit()

    def add_contents(self, contents: List[Any]):
        sql_batch = []
        for c in contents:
            d = c.to_dict() if hasattr(c, 'to_dict') else c
            sql_batch.append((d['chunk_hash'], d['content']))
        if sql_batch:
            self._cursor.executemany("INSERT OR IGNORE INTO contents VALUES (?, ?)", sql_batch)
            self._conn.commit()

    def find_chunk_id(self, file_path: str, byte_range: List[int]) -> Optional[str]:
        if not byte_range: return None
        # Tolleranza +/- 1 byte
        query = """
            SELECT id FROM nodes 
            WHERE file_path = ? AND byte_start <= ? + 1 AND byte_end >= ? - 1
            ORDER BY size ASC LIMIT 1
        """
        self._cursor.execute(query, (file_path, byte_range[0], byte_range[1]))
        row = self._cursor.fetchone()
        return row[0] if row else None

    def add_edge(self, source_id: str, target_id: str, relation_type: str, metadata: Dict[str, Any]):
        self._cursor.execute("INSERT INTO edges VALUES (?, ?, ?, ?)", (source_id, target_id, relation_type, json.dumps(metadata)))

    def ensure_external_node(self, node_id: str):
        try:
            self._cursor.execute("INSERT OR IGNORE INTO nodes (id, type) VALUES (?, ?)", (node_id, "external_library"))
        except Exception: pass

    def commit(self): self._conn.commit()

    def get_stats(self) -> Dict[str, int]:
        self.commit()
        
        total_nodes = self._cursor.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]
        
        # Nodi reali del codice sorgente
        source_nodes = self._cursor.execute(
            "SELECT COUNT(*) FROM nodes WHERE type != 'external_library'"
        ).fetchone()[0]
        
        # Nodi fantasma (librerie esterne create da SCIP)
        external_nodes = self._cursor.execute(
            "SELECT COUNT(*) FROM nodes WHERE type = 'external_library'"
        ).fetchone()[0]
        
        files = self._cursor.execute("SELECT COUNT(*) FROM files").fetchone()[0]
        contents = self._cursor.execute("SELECT COUNT(*) FROM contents").fetchone()[0]
        edges = self._cursor.execute("SELECT COUNT(*) FROM edges").fetchone()[0]
        
        return {
            "files": files,
            "total_nodes": total_nodes,
            "source_nodes": source_nodes,     # Chunk reali
            "external_nodes": external_nodes, # Simboli esterni
            "unique_contents": contents,
            "edges": edges
        }

    # Consumers
    def get_all_files(self):
        self._cursor.execute("SELECT * FROM files")
        cols = [d[0] for d in self._cursor.description]
        for row in self._cursor: yield dict(zip(cols, row))

    def get_all_nodes(self):
        self._cursor.execute("SELECT * FROM nodes")
        cols = [d[0] for d in self._cursor.description]
        for row in self._cursor: 
            d = dict(zip(cols, row))
            if d.get('metadata_json'):
                try:
                    d['metadata'] = json.loads(d['metadata_json'])
                except:
                    d['metadata'] = {}
                del d['metadata_json']
            yield d

    def get_all_contents(self):
        self._cursor.execute("SELECT * FROM contents")
        cols = [d[0] for d in self._cursor.description]
        for row in self._cursor: yield dict(zip(cols, row))

    def get_all_edges(self):
        self._cursor.execute("SELECT * FROM edges")
        cols = [d[0] for d in self._cursor.description]
        for row in self._cursor:
            d = dict(zip(cols, row))
            if d.get('metadata_json'):
                d['metadata'] = json.loads(d['metadata_json'])
                del d['metadata_json']
            yield d

    def close(self):
        try:
            self._conn.close()
            if os.path.exists(self._db_file): os.remove(self._db_file)
        except Exception: pass