import os
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

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI()

# Global indexer instance
indexer: Optional[CodebaseIndexer] = None

class IndexRequest(BaseModel):
    repo_path: str

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
                byte_range=[b_start, b_end]
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
                
                # Add data-id attribute for JS to pick up
                html_parts.append(f'<span class="{cls}" data-id="{node.id}" onclick="window.selectChunk(\'{node.id}\', event)">')
            else: # End
                html_parts.append('</span>')
            last_idx = idx

        if last_idx < len(source_bytes):
            html_parts.append(html.escape(source_bytes[last_idx:].decode('utf-8', errors='replace')))
            
        return "".join(html_parts)

# --- API ENDPOINTS ---

@app.post("/api/index")
def trigger_index(request: IndexRequest):
    global indexer
    repo_path = request.repo_path
    
    if not os.path.exists(repo_path):
        raise HTTPException(status_code=400, detail=f"Path not found: {repo_path}")
    
    try:
        if indexer: indexer.close()
        indexer = CodebaseIndexer(repo_path)
        indexer.index()
        stats = indexer.get_stats()
        return {"status": "success", "stats": stats}
    except Exception as e:
        logger.exception("Indexing failed")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/files")
def get_files():
    global indexer
    if not indexer:
        raise HTTPException(status_code=400, detail="No repository indexed.")
    try:
        files = list(indexer.get_files())
        # Just return list of files, we don't need to attach chunks here anymore
        # as we load them per file
        return {"files": files}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/file_view")
def get_file_view(path: str):
    global indexer
    if not indexer:
        raise HTTPException(status_code=400, detail="No repository indexed.")
    
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="File not found")
        
    try:
        raw_nodes = list(indexer.get_nodes())
        nodes = DbAdapter.adapt_nodes(raw_nodes)
        
        html_content = HtmlGenerator.generate_code_html(path, indexer.repo_path, nodes)
        
        return {"html": html_content}
    except Exception as e:
        logger.exception("Failed to generate file view")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/chunk/{chunk_id}/graph")
def get_chunk_graph(chunk_id: str):
    global indexer
    if not indexer:
        raise HTTPException(status_code=400, detail="No repository indexed.")
        
    try:
        # Reuse previous graph logic
        all_nodes_map = {n['id']: n for n in indexer.get_nodes()}
        nodes = {}
        edges = []
        
        # Load all contents for lookup
        content_map = {c['chunk_hash']: c['content'] for c in indexer.get_contents()}

        def add_node(nid):
            if nid not in nodes and nid in all_nodes_map:
                n_data = all_nodes_map[nid].copy()
                # Inject content
                if 'chunk_hash' in n_data and n_data['chunk_hash'] in content_map:
                    n_data['content'] = content_map[n_data['chunk_hash']]
                else:
                    n_data['content'] = None
                nodes[nid] = n_data
                return True
            return False

        if not add_node(chunk_id):
             raise HTTPException(status_code=404, detail="Chunk not found")

        all_edges = list(indexer.get_edges())
        
        # 1. First degree
        first_degree_ids = set()
        for edge in all_edges:
            if edge['source_id'] == chunk_id:
                target_id = edge['target_id']
                if target_id in all_nodes_map:
                    if all_nodes_map[target_id].get('type') != 'external_library':
                        add_node(target_id)
                        edges.append(edge)
                        first_degree_ids.add(target_id)
            elif edge['target_id'] == chunk_id:
                 source_id = edge['source_id']
                 if source_id in all_nodes_map:
                    if all_nodes_map[source_id].get('type') != 'external_library':
                        add_node(source_id)
                        edges.append(edge)
                        first_degree_ids.add(source_id)

        # 2. Second degree
        for start_id in first_degree_ids:
            for edge in all_edges:
                if edge['source_id'] == start_id:
                    target_id = edge['target_id']
                    if target_id != chunk_id and target_id in all_nodes_map:
                         if all_nodes_map[target_id].get('type') != 'external_library':
                            add_node(target_id)
                            edges.append(edge)
                elif edge['target_id'] == start_id:
                    source_id = edge['source_id']
                    if source_id != chunk_id and source_id in all_nodes_map:
                        if all_nodes_map[source_id].get('type') != 'external_library':
                            add_node(source_id)
                            edges.append(edge)

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
    uvicorn.run(app, host="0.0.0.0", port=8001)
