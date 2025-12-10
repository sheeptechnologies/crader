import os
import shutil
import logging
from typing import List, Optional, Dict, Any
from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import git

from src.code_graph_indexer.indexer import CodebaseIndexer
from src.code_graph_indexer.providers.embedding import OpenAIEmbeddingProvider, DummyEmbeddingProvider
from fastapi.responses import StreamingResponse
from src.code_graph_indexer.retriever import CodeRetriever
from src.code_graph_indexer.navigator import CodeNavigator
from debugger.agent_utils import get_agent
from debugger.database import get_storage, DB_URL

# Setup Logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("DebuggerServer")
# Ensure our library logs are shown
logging.getLogger("src.code_graph_indexer").setLevel(logging.INFO)

from dotenv import load_dotenv
load_dotenv()

# --- CONFIGURATION ---
# Set REPO_VOLUME to 'repos' directory in debugger folder
# This must be set BEFORE importing CodebaseIndexer (which imports config)
os.environ["REPO_VOLUME"] = os.path.join(os.path.dirname(__file__), "repos")

app = FastAPI(title="Sheep Debugger API")

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- Pydantic Models ---
class RepoRequest(BaseModel):
    path_or_url: str
    branch: str = "main"
    name: Optional[str] = None

class IndexRequest(BaseModel):
    force: bool = False

class EmbedRequest(BaseModel):
    provider: str = "openai" # openai, dummy
    model: str = "text-embedding-3-small"
    batch_size: int = 100

class SearchRequest(BaseModel):
    query: str
    repo_id: str
    limit: int = 10
    filters: Optional[Dict[str, Any]] = None

# --- Helpers ---
def clone_repo(url: str, target_dir: str, branch: str):
    if os.path.exists(target_dir):
        logger.info(f"Directory {target_dir} exists. Checking git status...")
        try:
            repo = git.Repo(target_dir)
            logger.info("Pulling latest changes...")
            repo.remotes.origin.pull()
            repo.git.checkout(branch)
        except git.exc.InvalidGitRepositoryError:
            logger.warning(f"Directory {target_dir} exists but is not a valid git repo. Cleaning up and re-cloning...")
            shutil.rmtree(target_dir)
            git.Repo.clone_from(url, target_dir, branch=branch)
        except Exception as e:
             logger.error(f"Git error: {e}")
             raise e
    else:
        logger.info(f"Cloning {url} to {target_dir}...")
        git.Repo.clone_from(url, target_dir, branch=branch)

# --- Endpoints ---

@app.get("/api/repos")
def list_repos():
    storage = get_storage()
    # We need to expose a method in storage to list all repos, 
    # but for now we can query the table directly if needed or add a method to SqliteGraphStorage.
    # Let's assume we can query directly using the cursor for now or add a method.
    # Since we can't easily modify the library code in this step without a separate tool call,
    # let's use a raw query here or rely on get_stats which counts them.
    # Ideally, we should add `get_all_repositories` to SqliteGraphStorage.
    # For now, let's use a raw query via the connection if possible, or just implement a workaround.
    # Accessing private _cursor is not ideal but works for a debugger tool.
    
    # Use connection pool for Postgres
    with storage.connector.get_connection() as conn:
        rows = conn.execute("SELECT * FROM repositories").fetchall()
        # rows are dicts because of dict_row factory in PostgresGraphStorage
        return rows

@app.post("/api/repos")
def add_repo(req: RepoRequest):
    # We no longer clone manually. GitVolumeManager handles it during indexing.
    # We just register the repo intent.
    
    repo_name = req.name or req.path_or_url.rstrip('/').split('/')[-1].replace(".git", "")
    
    # Register in DB
    storage = get_storage()
    
    # Use ensure_repository to register/get ID
    # Note: ensure_repository takes (url, branch, name)
    repo_id = storage.ensure_repository(req.path_or_url, req.branch, repo_name)
    
    return {"id": repo_id, "status": "registered", "url": req.path_or_url, "branch": req.branch}

@app.delete("/api/repos/{repo_id}")
def delete_repo(repo_id: str):
    storage = get_storage()
    # Check if exists
    repo = storage.get_repository(repo_id)
    if not repo:
        raise HTTPException(status_code=404, detail="Repo not found")
        
    # Delete from DB
    # We should probably delete related data too (files, nodes, etc.)
    # But PostgresGraphStorage might not have a cascade delete set up or a method for full cleanup.
    # Let's check if we can just delete from 'repositories' and rely on FK cascade?
    # PostgresGraphStorage schema usually has FKs. Let's assume cascade or manual cleanup.
    # For now, let's try deleting from repositories.
    
    with storage.connector.get_connection() as conn:
        # Delete related data first to be safe if no cascade
        conn.execute("DELETE FROM node_embeddings WHERE repo_id = %s", (repo_id,))
        conn.execute("DELETE FROM nodes_fts WHERE node_id IN (SELECT id FROM nodes WHERE file_id IN (SELECT id FROM files WHERE repo_id = %s))", (repo_id,))
        conn.execute("DELETE FROM edges WHERE source_id IN (SELECT id FROM nodes WHERE file_id IN (SELECT id FROM files WHERE repo_id = %s))", (repo_id,))
        conn.execute("DELETE FROM nodes WHERE file_id IN (SELECT id FROM files WHERE repo_id = %s)", (repo_id,))
        conn.execute("DELETE FROM files WHERE repo_id = %s", (repo_id,))
        conn.execute("DELETE FROM repositories WHERE id = %s", (repo_id,))
        
    return {"status": "deleted", "id": repo_id}

@app.post("/api/repos/{repo_id}/index")
def index_repo(repo_id: str, req: IndexRequest, background_tasks: BackgroundTasks):
    storage = get_storage()
    repo = storage.get_repository(repo_id)
    if not repo:
        raise HTTPException(status_code=404, detail="Repo not found")
    
    # New Indexer takes url and branch, not local path
    repo_url = repo['url']
    branch = repo['branch']

    def _run_index():
        try:
            # CodebaseIndexer now manages its own storage/connector
            # We pass DB_URL to it so it can connect
            indexer = CodebaseIndexer(repo_url, branch, db_url=DB_URL)
            try:
                snapshot_id = indexer.index(force=req.force)
                logger.info(f"Indexing completed. Snapshot ID: {snapshot_id}")
            finally:
                indexer.close()
        except Exception as e:
            logger.error(f"Indexing failed: {e}")

    background_tasks.add_task(_run_index)
    return {"status": "indexing_started"}

@app.post("/api/repos/{repo_id}/embed")
def embed_repo(repo_id: str, req: EmbedRequest, background_tasks: BackgroundTasks):
    storage = get_storage()
    repo = storage.get_repository(repo_id)
    if not repo:
        raise HTTPException(status_code=404, detail="Repo not found")
        
    repo_url = repo['url']
    branch = repo['branch']
    
    provider = None
    try:
        if req.provider == "openai":
            provider = OpenAIEmbeddingProvider(model=req.model)
        else:
            provider = DummyEmbeddingProvider()
    except ValueError as e:
        # Likely missing API Key
        raise HTTPException(status_code=400, detail=str(e))
    except ImportError:
        raise HTTPException(status_code=500, detail="OpenAI library not installed.")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to initialize provider: {e}")

    def _run_embed():
        try:
            indexer = CodebaseIndexer(repo_url, branch, db_url=DB_URL)
            try:
                # Consume generator
                count = 0
                # Resolve active snapshot automatically inside embed if not passed
                for item in indexer.embed(provider, batch_size=req.batch_size, debug=True):
                    count += 1
                    if count % 10 == 0:
                        logger.info(f"🤖 Embedding progress: {count} items processed...")
                
                logger.info(f"✅ Embedding finished. Total items: {count}")
            finally:
                indexer.close()
        except Exception as e:
            logger.error(f"Embedding failed: {e}")

    background_tasks.add_task(_run_embed)
    return {"status": "embedding_started"}

@app.get("/api/repos/{repo_id}/files")
def get_file_tree(repo_id: str):
    storage = get_storage()
    
    # Get active snapshot
    snapshot_id = storage.get_active_snapshot_id(repo_id)
    if not snapshot_id:
        return [] # Or raise error
        
    # Use manifest
    manifest = storage.get_snapshot_manifest(snapshot_id)
    
    # Convert manifest (nested dict) to list of nodes for the frontend tree component
    # Frontend expects: [{name, path, type, children: [...]}, ...]
    
    def convert_manifest(node_name, node_data, parent_path=""):
        current_path = f"{parent_path}/{node_name}" if parent_path else node_name
        
        children = []
        if node_data.get("children"):
            for child_name, child_data in node_data["children"].items():
                children.append(convert_manifest(child_name, child_data, current_path))
        
        # Sort children: directories first, then files
        children.sort(key=lambda x: (x["type"] != "directory", x["name"]))

        return {
            "name": node_name,
            "path": current_path,
            "type": "directory" if node_data["type"] == "dir" else "file",
            "children": children if node_data["type"] == "dir" else None,
            # Manifest might not have category/language/status, but that's fine for now
            "category": None,
            "language": None,
            "status": "success"
        }

    # Manifest root is a dict {"type": "dir", "children": {...}} representing the root
    # We want to return the list of children of the root
    root_children = []
    if manifest and manifest.get("children"):
        for name, data in manifest["children"].items():
             root_children.append(convert_manifest(name, data, ""))
             
    root_children.sort(key=lambda x: (x["type"] != "directory", x["name"]))
    return root_children

@app.get("/api/repos/{repo_id}/file_content")
def get_file_content(repo_id: str, path: str):
    storage = get_storage()
    
    snapshot_id = storage.get_active_snapshot_id(repo_id)
    if not snapshot_id:
        raise HTTPException(status_code=404, detail="No active snapshot for this repo")

    # Use get_file_content_range to fetch full content from DB
    content = storage.get_file_content_range(snapshot_id, path)
    if content is None:
         raise HTTPException(status_code=404, detail="File not found in snapshot")
         
    # Get chunks for this file
    with storage.connector.get_connection() as conn:
        # Join with files to filter by path and snapshot
        rows = conn.execute("""
            SELECT n.id, n.start_line, n.end_line, n.byte_start, n.byte_end, n.metadata, n.chunk_hash
            FROM nodes n
            JOIN files f ON n.file_id = f.id
            WHERE f.snapshot_id = %s AND f.path = %s
        """, (snapshot_id, path)).fetchall()
    
    chunks = []
    for row in rows:
        d = dict(row)
        if isinstance(d.get("metadata"), str):
            import json
            try:
                d["metadata"] = json.loads(d["metadata"])
            except:
                d["metadata"] = {}
        chunks.append(d)
        
    return {"content": content, "chunks": chunks}

@app.get("/api/chunks/{chunk_id}/graph")
def get_chunk_graph(chunk_id: str):
    logger.info(f"Fetching graph for chunk_id: {chunk_id}")
    storage = get_storage()
    
    # Get the node itself
    with storage.connector.get_connection() as conn:
        row = conn.execute("""
            SELECT n.id, n.file_path, n.start_line, n.end_line, c.content, n.metadata
            FROM nodes n
            LEFT JOIN contents c ON n.chunk_hash = c.chunk_hash
            WHERE n.id = %s
        """, (chunk_id,)).fetchone()
    
    if not row:
        logger.error(f"Chunk {chunk_id} not found in DB")
        raise HTTPException(status_code=404, detail=f"Chunk {chunk_id} not found")
    
    import json
    # Handle metadata if string
    meta = row['metadata']
    if isinstance(meta, str):
        meta = json.loads(meta)

    center_node = {
        "id": str(row['id']),
        "file_path": row['file_path'],
        "start_line": row['start_line'],
        "end_line": row['end_line'],
        "content": row['content'],
        "metadata": meta or {},
        "type": "center"
    }
    
    nodes = [center_node]
    edges = []
    
    # Get outgoing edges (depth 1)
    outgoing = storage.get_outgoing_calls(chunk_id, limit=50)
    for out in outgoing:
        # We need more info for the target node to display code
        # get_outgoing_calls returns target_id, file, line, relation, symbol
        # Let's fetch content for these if possible, or just basic info
        target_id = out['target_id']
        
        # Fetch target node details
        # Fetch target node details
        with storage.connector.get_connection() as conn:
            t_row = conn.execute("""
                SELECT n.id, n.file_path, n.start_line, n.end_line, c.content, n.metadata
                FROM nodes n
                LEFT JOIN contents c ON n.chunk_hash = c.chunk_hash
                WHERE n.id = %s
            """, (target_id,)).fetchone()
        
        if t_row:
            meta = t_row['metadata']
            if isinstance(meta, str): meta = json.loads(meta)
            
            nodes.append({
                "id": str(t_row['id']),
                "file_path": t_row['file_path'],
                "start_line": t_row['start_line'],
                "end_line": t_row['end_line'],
                "content": t_row['content'],
                "metadata": meta or {},
                "type": "neighbor"
            })
            edges.append({
                "source": chunk_id,
                "target": target_id,
                "relation": out['relation']
            })

    # Get incoming edges (depth 1)
    incoming = storage.get_incoming_references(chunk_id, limit=50)
    for inc in incoming:
        source_id = inc['source_id']
        
        # Fetch source node details
        # Fetch source node details
        with storage.connector.get_connection() as conn:
            s_row = conn.execute("""
                SELECT n.id, n.file_path, n.start_line, n.end_line, c.content, n.metadata
                FROM nodes n
                LEFT JOIN contents c ON n.chunk_hash = c.chunk_hash
                WHERE n.id = %s
            """, (source_id,)).fetchone()
        
        if s_row:
            meta = s_row['metadata']
            if isinstance(meta, str): meta = json.loads(meta)

            nodes.append({
                "id": str(s_row['id']),
                "file_path": s_row['file_path'],
                "start_line": s_row['start_line'],
                "end_line": s_row['end_line'],
                "content": s_row['content'],
                "metadata": meta or {},
                "type": "neighbor"
            })
            edges.append({
                "source": source_id,
                "target": chunk_id,
                "relation": inc['relation']
            })

    # Explicitly fetch child_of relations (parent/child)
    # Check if this node is a child of another node
    with storage.connector.get_connection() as conn:
        # Parents (this node is source, relation is child_of)
        parents = conn.execute("""
            SELECT target_id FROM edges WHERE source_id = %s AND relation_type = 'child_of'
        """, (chunk_id,)).fetchall()
        
        for p in parents:
            pid = p['target_id']
            # Fetch parent details
            p_row = conn.execute("""
                SELECT n.id, n.file_path, n.start_line, n.end_line, c.content, n.metadata
                FROM nodes n
                LEFT JOIN contents c ON n.chunk_hash = c.chunk_hash
                WHERE n.id = %s
            """, (pid,)).fetchone()
            
            if p_row:
                meta = p_row['metadata']
                if isinstance(meta, str): meta = json.loads(meta)
                nodes.append({
                    "id": str(p_row['id']),
                    "file_path": p_row['file_path'],
                    "start_line": p_row['start_line'],
                    "end_line": p_row['end_line'],
                    "content": p_row['content'],
                    "metadata": meta or {},
                    "type": "parent"
                })
                edges.append({
                    "source": chunk_id,
                    "target": pid,
                    "relation": "child_of"
                })

        # Children (this node is target, relation is child_of)
        children = conn.execute("""
            SELECT source_id FROM edges WHERE target_id = %s AND relation_type = 'child_of'
        """, (chunk_id,)).fetchall()
        
        for c in children:
            cid = c['source_id']
            # Fetch child details
            c_row = conn.execute("""
                SELECT n.id, n.file_path, n.start_line, n.end_line, c.content, n.metadata
                FROM nodes n
                LEFT JOIN contents c ON n.chunk_hash = c.chunk_hash
                WHERE n.id = %s
            """, (cid,)).fetchone()
            
            if c_row:
                meta = c_row['metadata']
                if isinstance(meta, str): meta = json.loads(meta)
                nodes.append({
                    "id": str(c_row['id']),
                    "file_path": c_row['file_path'],
                    "start_line": c_row['start_line'],
                    "end_line": c_row['end_line'],
                    "content": c_row['content'],
                    "metadata": meta or {},
                    "type": "child"
                })
                edges.append({
                    "source": cid,
                    "target": chunk_id,
                    "relation": "child_of"
                })
            
    # Deduplicate nodes
    unique_nodes = {n['id']: n for n in nodes}.values()
    
    return {"nodes": list(unique_nodes), "edges": edges}

@app.get("/api/chunks/{chunk_id}/impact")
def get_chunk_impact(chunk_id: str):
    storage = get_storage()
    navigator = CodeNavigator(storage)
    
    try:
        impact_data = navigator.analyze_impact(chunk_id)
        
        # Convert to graph format
        nodes = []
        edges = []
        
        # Add center node
        with storage.connector.get_connection() as conn:
            row = conn.execute("""
                SELECT n.id, n.file_path, n.start_line, n.end_line, c.content, n.metadata
                FROM nodes n
                LEFT JOIN contents c ON n.chunk_hash = c.chunk_hash
                WHERE n.id = %s
            """, (chunk_id,)).fetchone()
            if row:
                nodes.append({
                    "id": str(row['id']),
                    "file_path": row['file_path'],
                    "start_line": row['start_line'],
                    "end_line": row['end_line'],
                    "content": row['content'],
                    "metadata": row['metadata'], # navigator handles enrichment if needed, but here we just pass raw
                    "type": "center"
                })

        for ref in impact_data:
            # ref is {source_id, relation, file, line}
            # We need to fetch full node details for the source
             with storage.connector.get_connection() as conn:
                s_row = conn.execute("""
                    SELECT n.id, n.file_path, n.start_line, n.end_line, c.content, n.metadata
                    FROM nodes n
                    LEFT JOIN contents c ON n.chunk_hash = c.chunk_hash
                    WHERE n.id = %s
                """, (ref['source_id'],)).fetchone()
                
                if s_row:
                    nodes.append({
                        "id": str(s_row['id']),
                        "file_path": s_row['file_path'],
                        "start_line": s_row['start_line'],
                        "end_line": s_row['end_line'],
                        "content": s_row['content'],
                        "metadata": s_row['metadata'],
                        "type": "impact"
                    })
                    edges.append({
                        "source": ref['source_id'],
                        "target": chunk_id,
                        "relation": ref['relation']
                    })
        
        return {"nodes": nodes, "edges": edges}
    except Exception as e:
        logger.error(f"Error in impact analysis: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/chunks/{chunk_id}/pipeline")
def get_chunk_pipeline(chunk_id: str):
    storage = get_storage()
    navigator = CodeNavigator(storage)
    
    try:
        pipeline_tree = navigator.visualize_pipeline(chunk_id)
        # Flatten tree to graph
        nodes = []
        edges = []
        visited = set()

        def _process_tree(parent_id, children_dict):
            if not children_dict: return
            
            for child_id, child_data in children_dict.items():
                if child_id not in visited:
                    visited.add(child_id)
                    # Fetch node details
                    with storage.connector.get_connection() as conn:
                        c_row = conn.execute("""
                            SELECT n.id, n.file_path, n.start_line, n.end_line, c.content, n.metadata
                            FROM nodes n
                            LEFT JOIN contents c ON n.chunk_hash = c.chunk_hash
                            WHERE n.id = %s
                        """, (child_id,)).fetchone()
                    
                    if c_row:
                        nodes.append({
                            "id": str(c_row['id']),
                            "file_path": c_row['file_path'],
                            "start_line": c_row['start_line'],
                            "end_line": c_row['end_line'],
                            "content": c_row['content'],
                            "metadata": c_row['metadata'],
                            "type": "pipeline"
                        })
                
                edges.append({
                    "source": parent_id,
                    "target": child_id,
                    "relation": child_data.get('type', 'calls')
                })
                
                if child_data.get('children'):
                    _process_tree(child_id, child_data['children'])

        # Add root node
        with storage.connector.get_connection() as conn:
            row = conn.execute("""
                SELECT n.id, n.file_path, n.start_line, n.end_line, c.content, n.metadata
                FROM nodes n
                LEFT JOIN contents c ON n.chunk_hash = c.chunk_hash
                WHERE n.id = %s
            """, (chunk_id,)).fetchone()
            if row:
                nodes.append({
                    "id": str(row['id']),
                    "file_path": row['file_path'],
                    "start_line": row['start_line'],
                    "end_line": row['end_line'],
                    "content": row['content'],
                    "metadata": row['metadata'],
                    "type": "center"
                })
                visited.add(chunk_id)
        
        _process_tree(chunk_id, pipeline_tree.get('call_graph', {}))
        
        return {"nodes": nodes, "edges": edges}
    except Exception as e:
        logger.error(f"Error in pipeline visualization: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/chunks/{chunk_id}/neighbors")
def get_chunk_neighbors(chunk_id: str):
    storage = get_storage()
    navigator = CodeNavigator(storage)
    
    try:
        nodes = []
        edges = []
        
        # Add center node
        with storage.connector.get_connection() as conn:
            row = conn.execute("""
                SELECT n.id, n.file_path, n.start_line, n.end_line, c.content, n.metadata
                FROM nodes n
                LEFT JOIN contents c ON n.chunk_hash = c.chunk_hash
                WHERE n.id = %s
            """, (chunk_id,)).fetchone()
            if row:
                nodes.append({
                    "id": str(row['id']),
                    "file_path": row['file_path'],
                    "start_line": row['start_line'],
                    "end_line": row['end_line'],
                    "content": row['content'],
                    "metadata": row['metadata'],
                    "type": "center"
                })

        # Get Prev
        prev_chunk = navigator.read_neighbor_chunk(chunk_id, direction="prev")
        if prev_chunk:
            nodes.append({
                "id": str(prev_chunk['id']),
                "file_path": prev_chunk['file_path'],
                "start_line": prev_chunk['start_line'],
                "end_line": prev_chunk['end_line'],
                "content": prev_chunk.get('content', ''),
                "metadata": prev_chunk.get('metadata', {}),
                "type": "neighbor"
            })
            edges.append({
                "source": str(prev_chunk['id']),
                "target": chunk_id,
                "relation": "previous"
            })

        # Get Next
        next_chunk = navigator.read_neighbor_chunk(chunk_id, direction="next")
        if next_chunk:
            nodes.append({
                "id": str(next_chunk['id']),
                "file_path": next_chunk['file_path'],
                "start_line": next_chunk['start_line'],
                "end_line": next_chunk['end_line'],
                "content": next_chunk.get('content', ''),
                "metadata": next_chunk.get('metadata', {}),
                "type": "neighbor"
            })
            edges.append({
                "source": chunk_id,
                "target": str(next_chunk['id']),
                "relation": "next"
            })
            
        return {"nodes": nodes, "edges": edges}
    except Exception as e:
        logger.error(f"Error in neighbors analysis: {e}")
        raise HTTPException(status_code=500, detail=str(e))

class ChatRequest(BaseModel):
    repo_id: str
    message: str
    thread_id: str

@app.post("/api/agent/chat")
def chat_agent(req: ChatRequest):
    agent = get_agent(req.repo_id)
    
    return StreamingResponse(
        agent.stream_chat(req.message, req.thread_id),
        media_type="application/x-ndjson"
    )

@app.get("/api/chunks/{chunk_id}/parent")
def get_chunk_parent(chunk_id: str):
    storage = get_storage()
    navigator = CodeNavigator(storage)
    
    try:
        nodes = []
        edges = []
        
        # Add center node
        with storage.connector.get_connection() as conn:
            row = conn.execute("""
                SELECT n.id, n.file_path, n.start_line, n.end_line, c.content, n.metadata
                FROM nodes n
                LEFT JOIN contents c ON n.chunk_hash = c.chunk_hash
                WHERE n.id = %s
            """, (chunk_id,)).fetchone()
            if row:
                nodes.append({
                    "id": str(row['id']),
                    "file_path": row['file_path'],
                    "start_line": row['start_line'],
                    "end_line": row['end_line'],
                    "content": row['content'],
                    "metadata": row['metadata'],
                    "type": "center"
                })

        parent = navigator.read_parent_chunk(chunk_id)
        if parent:
            nodes.append({
                "id": str(parent['id']),
                "file_path": parent['file_path'],
                "start_line": parent['start_line'],
                "end_line": parent['end_line'],
                "content": parent.get('content', ''),
                "metadata": parent.get('metadata', {}),
                "type": "parent"
            })
            edges.append({
                "source": chunk_id,
                "target": str(parent['id']),
                "relation": "child_of"
            })
            
        return {"nodes": nodes, "edges": edges}
    except Exception as e:
        logger.error(f"Error in parent analysis: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/search")
def search(req: SearchRequest):
    storage = get_storage()
    
    # For search we need an embedder if we want hybrid/vector search
    # We'll assume OpenAI for now if not specified, or fallback to keyword
    # But CodeRetriever needs an embedder instance.
    
    # If we don't have an API key, we might fail on vector search.
    # Let's try to initialize OpenAI, if fails use Dummy.
    try:
        embedder = OpenAIEmbeddingProvider()
    except:
        embedder = DummyEmbeddingProvider()
        
    retriever = CodeRetriever(storage, embedder)
    
    results = retriever.retrieve(
        query=req.query,
        repo_id=req.repo_id,
        limit=req.limit,
        filters=req.filters,
        strategy="hybrid" # Default to hybrid
    )
    
    return results

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8019)
