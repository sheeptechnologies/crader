import os
os.environ["TOKENIZERS_PARALLELISM"] = "false"
import sys
import logging
import html
import json
from typing import List, Dict, Any, Optional
from dataclasses import dataclass, asdict
from fastapi import FastAPI, HTTPException, Body
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

# Add src to path to import CodebaseIndexer
sys.path.append(os.path.join(os.path.dirname(__file__), "..", "src"))

from code_graph_indexer.indexer import CodebaseIndexer
from code_graph_indexer.storage.sqlite import SqliteGraphStorage
from code_graph_indexer.retriever import CodeRetriever
from code_graph_indexer.providers.embedding import FastEmbedProvider, DummyEmbeddingProvider

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI()

# Global instances
storage: Optional[SqliteGraphStorage] = None
retriever: Optional[CodeRetriever] = None

def get_components():
    global storage, retriever
    if storage is None:
        # Initialize persistent storage
        db_path = os.path.join(os.path.dirname(__file__), "..", "sheep_index.db")
        storage = SqliteGraphStorage(db_path)
        
        # Initialize Embedder (Fallback logic)
        try:
            embedder = FastEmbedProvider()
        except ImportError:
            embedder = DummyEmbeddingProvider()
            logger.warning("FastEmbed not installed. Using DummyEmbeddingProvider.")
        except Exception as e:
            logger.warning(f"Failed to load FastEmbed: {e}. Using DummyEmbeddingProvider.")
            embedder = DummyEmbeddingProvider()
            
        retriever = CodeRetriever(storage, embedder)
    return storage, retriever, embedder

def get_components():
    s, r, _ = get_components_full()
    return s, r

def get_components_full():
    global storage, retriever
    if storage is None:
        # Initialize persistent storage
        db_path = os.path.join(os.path.dirname(__file__), "..", "sheep_index.db")
        storage = SqliteGraphStorage(db_path)
        
        # Initialize Embedder (Fallback logic)
        try:
            embedder = FastEmbedProvider()
        except ImportError:
            logger.warning("FastEmbed not installed. Using DummyEmbeddingProvider.")
            embedder = DummyEmbeddingProvider()
        except Exception as e:
            logger.warning(f"Failed to load FastEmbed: {e}. Using DummyEmbeddingProvider.")
            embedder = DummyEmbeddingProvider()
            
        retriever = CodeRetriever(storage, embedder)
        return storage, retriever, embedder
    # We need to retrieve the embedder from the retriever if already initialized
    # But retriever.embedder is protected/private? No, it's public in python usually.
    # Let's check retriever.py if needed, but assuming it's accessible or we store it.
    # Actually, let's just store it in a global variable too to be safe.
    return storage, retriever, retriever.embedder

class IndexRequest(BaseModel):
    repo_path: str

class SearchRequest(BaseModel):
    query: str
    repo_id: str
    strategy: str = "hybrid"

# --- ADAPTERS & GENERATORS (Adapted from User Snippet) ---

class DbAdapter:
    @dataclass
    class NodeView:
        id: str
        file_path: str
        chunk_hash: str
        type: str
        start_line: int
        end_line: int
        byte_range: List[int]
        metadata: Dict[str, Any]

    @staticmethod
    def adapt_nodes(db_nodes: List[Dict]) -> List[Any]:
        res = []
        for n in db_nodes:
            b_start = n.get('byte_start')
            b_end = n.get('byte_end')
            if b_start is None or b_end is None:
                continue 
            
            res.append(DbAdapter.NodeView(
                id=n['id'],
                file_path=n.get('file_path'), 
                chunk_hash=n.get('chunk_hash', ''),
                type=n['type'],
                start_line=n.get('start_line', 0),
                end_line=n.get('end_line', 0),
                byte_range=[b_start, b_end],
                metadata=n.get('metadata', {})
            ))
        return res

class HtmlGenerator:
    @staticmethod
    def generate_code_html(file_path: str, repo_root: str, all_nodes: List[Any]) -> str:
        # Calculate relative path to match nodes
        rel_path = os.path.relpath(file_path, repo_root)
        
        # Filter nodes for this file
        file_nodes = [n for n in all_nodes if n.file_path == rel_path]
        file_nodes.sort(key=lambda x: x.byte_range[0])
        
        try:
            with open(file_path, 'rb') as f:
                source_bytes = f.read()
        except Exception:
            return "Error reading file"

        events = []
        for n in file_nodes:
            events.append((n.byte_range[0], 1, n))  # Start
            events.append((n.byte_range[1], -1, n)) # End
        
        # Sort events: 
        # 1. Position
        # 2. End (-1) before Start (1) at same position (to close inner before opening next? No, close inner before close outer)
        # Wait, standard nesting: 
        # [Outer [Inner ]]
        # Start Outer, Start Inner, End Inner, End Outer.
        # If they end at same place: End Inner (-1) before End Outer (-1).
        # If Start and End at same place (empty?): Start then End.
        # User logic:
        # type_rank = 0 if type == -1 else 1  => End (0) comes before Start (1) at same pos.
        # This handles: [Chunk1][Chunk2] -> End1 then Start2. Correct.
        # len_rank = -length if type == 1 else length
        # Start: Longest first (Outer starts before Inner if same pos).
        # End: Shortest first (Inner ends before Outer if same pos).
        
        def sort_key(evt):
            pos, type, node = evt
            length = node.byte_range[1] - node.byte_range[0]
            type_rank = 0 if type == -1 else 1
            len_rank = -length if type == 1 else length
            return (pos, type_rank, len_rank)

        events.sort(key=sort_key)

        html_parts = []
        last_idx = 0
        
        for idx, type, node in events:
            if idx > last_idx:
                segment = source_bytes[last_idx:idx].decode('utf-8', errors='replace')
                html_parts.append(html.escape(segment))
            
            if type == 1: # Start
                cls = "chunk"
                if "class" in node.type: cls += " type-class"
                elif "function" in node.type or "method" in node.type: cls += " type-func"
                
                # Add data-id and data-type attributes for JS and CSS
                html_parts.append(f'<span class="{cls}" data-id="{node.id}" data-type="{node.type}" onclick="window.selectChunk(\'{node.id}\', event)">')
            else: # End
                html_parts.append('</span>')
            last_idx = idx

        if last_idx < len(source_bytes):
            html_parts.append(html.escape(source_bytes[last_idx:].decode('utf-8', errors='replace')))
            
        return "".join(html_parts)

# --- API ENDPOINTS ---

@app.get("/api/repositories")
def get_repositories():
    store, _ = get_components()
    try:
        # Assuming storage has a method to get all repositories or we query the table directly
        # The SqliteGraphStorage has get_stats but maybe not list_repos.
        # Let's check if we can add a helper or query directly.
        # storage._cursor is available but internal.
        # Let's use a raw query for now if no method exists, or add one.
        # Looking at sqlite.py, there isn't a get_all_repositories method exposed publicly 
        # but there is a table 'repositories'.
        # We can use the internal cursor or add a method. 
        # Since I cannot easily modify sqlite.py right now without context switch, 
        # I will use the internal cursor for this debug tool.
        store._cursor.execute("SELECT * FROM repositories")
        cols = [d[0] for d in store._cursor.description]
        repos = [dict(zip(cols, row)) for row in store._cursor]
        return {"repositories": repos}
    except Exception as e:
        logger.exception("Failed to list repositories")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/search")
def search_code(request: SearchRequest):
    _, retr = get_components()
    try:
        results = retr.retrieve(
            query=request.query,
            repo_id=request.repo_id,
            limit=10,
            strategy=request.strategy
        )
        return {"results": [r.to_dict() for r in results]}
    except Exception as e:
        logger.exception("Search failed")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/index")
def trigger_index(request: IndexRequest):
    store, _ = get_components()
    repo_path = request.repo_path.strip()
    
    if not os.path.exists(repo_path):
        raise HTTPException(status_code=400, detail=f"Path not found: {repo_path}")
    
    try:
        # Use the persistent storage
        indexer = CodebaseIndexer(repo_path, store)
        indexer.index()
        
        # Run embeddings
        _, _, embedder = get_components_full()
        # We need to consume the generator
        for _ in indexer.embed(embedder):
            pass
            
        stats = store.get_stats()
        return {"status": "success", "stats": stats}
    except Exception as e:
        logger.exception("Indexing failed")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/files")
def get_files(repo_id: Optional[str] = None):
    store, _ = get_components()
    try:
        if repo_id:
            store._cursor.execute("SELECT * FROM files WHERE repo_id = ?", (repo_id,))
        else:
            store._cursor.execute("SELECT * FROM files")
            
        cols = [d[0] for d in store._cursor.description]
        files = [dict(zip(cols, row)) for row in store._cursor]
        return {"files": files}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/file_view")
def get_file_view(path: str, repo_id: Optional[str] = None):
    store, _ = get_components()
    
    # If repo_id is provided, we can find the local path from the repo record
    # to read the file content.
    local_repo_path = ""
    if repo_id:
        store._cursor.execute("SELECT local_path FROM repositories WHERE id = ?", (repo_id,))
        row = store._cursor.fetchone()
        if row and row[0]:
            local_repo_path = row[0]
    
    # Fallback or check if path is absolute
    full_path = path
    if local_repo_path and not os.path.isabs(path):
        full_path = os.path.join(local_repo_path, path)
    
    if not os.path.exists(full_path):
        # Try to find it via the store if we can't find it on disk (maybe remote?)
        # For now, assume local access is required for file view content
        raise HTTPException(status_code=404, detail=f"File not found: {full_path}")
        
    try:
        # Get nodes for this file. Filter by repo_id if possible to avoid collisions.
        # We use a raw query to be efficient and specific.
        sql = "SELECT * FROM nodes WHERE file_path = ?"
        params = [path] # path in DB is relative
        
        # If path passed is absolute, make it relative if we have local_repo_path
        rel_path = path
        if local_repo_path and path.startswith(local_repo_path):
            rel_path = os.path.relpath(path, local_repo_path)
            sql = "SELECT * FROM nodes WHERE file_path = ?"
            params = [rel_path]

        # Actually, nodes table doesn't have repo_id directly, but we can join files.
        # But for now let's assume file_path is unique enough or we just get all nodes for that path.
        # To be safe:
        if repo_id:
            sql = """
                SELECT n.* FROM nodes n
                JOIN files f ON n.file_path = f.path
                WHERE n.file_path = ? AND f.repo_id = ?
            """
            params = [rel_path, repo_id]

        store._cursor.execute(sql, params)
        cols = [d[0] for d in store._cursor.description]
        raw_nodes = [dict(zip(cols, row)) for row in store._cursor]
        
        # Parse metadata_json if present (it is in raw dict from sqlite)
        for n in raw_nodes:
            if n.get('metadata_json'):
                try: n['metadata'] = json.loads(n['metadata_json'])
                except: n['metadata'] = {}
                del n['metadata_json']

        nodes = DbAdapter.adapt_nodes(raw_nodes)
        
        html_content = HtmlGenerator.generate_code_html(full_path, local_repo_path or os.path.dirname(full_path), nodes)
        
        return {"html": html_content}
    except Exception as e:
        logger.exception("Failed to generate file view")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/chunk/{chunk_id}/graph")
def get_chunk_graph(chunk_id: str, repo_id: Optional[str] = None):
    store, _ = get_components()
        
    try:
        # 1. Get the center node
        store._cursor.execute("SELECT * FROM nodes WHERE id = ?", (chunk_id,))
        row = store._cursor.fetchone()
        if not row:
             raise HTTPException(status_code=404, detail="Chunk not found")
        
        cols = [d[0] for d in store._cursor.description]
        center_node = dict(zip(cols, row))
        if center_node.get('metadata_json'):
            center_node['metadata'] = json.loads(center_node['metadata_json'])
            del center_node['metadata_json']
            
        # 2. Get edges (1st and 2nd degree)
        # We can use a recursive CTE or just simple queries.
        # Let's use the logic from before but adapted for SQL to avoid loading ALL nodes.
        
        nodes = {chunk_id: center_node}
        edges = []
        
        # Helper to fetch nodes
        def fetch_nodes(nids):
            if not nids: return
            placeholders = ",".join(["?"] * len(nids))
            store._cursor.execute(f"SELECT * FROM nodes WHERE id IN ({placeholders})", list(nids))
            c = [d[0] for d in store._cursor.description]
            for r in store._cursor:
                n = dict(zip(c, r))
                if n.get('metadata_json'):
                    n['metadata'] = json.loads(n['metadata_json'])
                    del n['metadata_json']
                nodes[n['id']] = n

        # 1st degree edges
        store._cursor.execute("SELECT * FROM edges WHERE source_id = ? OR target_id = ?", (chunk_id, chunk_id))
        c_edge = [d[0] for d in store._cursor.description]
        first_edges = [dict(zip(c_edge, r)) for r in store._cursor]
        
        neighbor_ids = set()
        for e in first_edges:
            if e['metadata_json']: e['metadata'] = json.loads(e['metadata_json'])
            del e['metadata_json']
            edges.append(e)
            neighbor_ids.add(e['source_id'])
            neighbor_ids.add(e['target_id'])
            
        neighbor_ids.discard(chunk_id)
        fetch_nodes(neighbor_ids)
        
        # 2nd degree edges (between neighbors)
        if neighbor_ids:
            placeholders = ",".join(["?"] * len(neighbor_ids))
            store._cursor.execute(f"""
                SELECT * FROM edges 
                WHERE source_id IN ({placeholders}) AND target_id IN ({placeholders})
            """, list(neighbor_ids) * 2)
            
            second_edges = [dict(zip(c_edge, r)) for r in store._cursor]
            for e in second_edges:
                if e['metadata_json']: e['metadata'] = json.loads(e['metadata_json'])
                del e['metadata_json']
                edges.append(e)

        # 3. Fetch content for all nodes
        chunk_hashes = [n['chunk_hash'] for n in nodes.values() if n.get('chunk_hash')]
        if chunk_hashes:
            placeholders = ",".join(["?"] * len(chunk_hashes))
            store._cursor.execute(f"SELECT chunk_hash, content FROM contents WHERE chunk_hash IN ({placeholders})", chunk_hashes)
            content_map = {row[0]: row[1] for row in store._cursor}
            
            for n in nodes.values():
                if n.get('chunk_hash') in content_map:
                    n['content'] = content_map[n['chunk_hash']]
                else:
                    n['content'] = None

        return {
            "nodes": list(nodes.values()),
            "edges": edges
        }

    except Exception as e:
        logger.exception("Failed to get graph")
        raise HTTPException(status_code=500, detail=str(e))

app.mount("/", StaticFiles(directory=os.path.join(os.path.dirname(__file__), "static"), html=True), name="static")

if __name__ == "__main__":
    import uvicorn
    # Changed port to 8001 as requested by user previously
    uvicorn.run(app, host="0.0.0.0", port=8017)

