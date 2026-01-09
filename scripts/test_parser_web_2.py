import os
import sys
import json
import argparse
import html
import webbrowser
import logging
import socket
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse
from typing import List, Dict, Any
from dataclasses import dataclass

# --- CONFIGURAZIONE PATH ---
current_dir = os.path.dirname(os.path.abspath(__file__))
src_dir = os.path.abspath(os.path.join(current_dir, '..', 'src'))
if src_dir not in sys.path:
    sys.path.insert(0, src_dir)

# Configurazione logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger("VisualDebugger")

try:
    from code_graph_indexer import CodebaseIndexer
except ImportError as e:
    logger.error(f"[FATAL] Errore importazione libreria: {e}")
    sys.exit(1)

# --- STATO GLOBALE ---
SERVER_PORT = 8000
target_file_abs = ""
repo_root_abs = ""

class DbAdapter:
    """
    Converte i dizionari grezzi di SQLite in oggetti strutturati per il frontend.
    """
    
    @dataclass
    class NodeView:
        id: str
        file_path: str
        chunk_hash: str
        type: str
        start_line: int
        end_line: int
        byte_range: List[int]

    @dataclass
    class RelView:
        source_id: str
        target_id: str
        relation_type: str
        metadata: Dict
        source_file: str = "UNKNOWN"
        target_file: str = "UNKNOWN"

    @staticmethod
    def adapt_nodes(db_nodes: List[Dict]) -> List[Any]:
        res = []
        for n in db_nodes:
            # Gestione sicura dei campi DB
            b_start = n.get('byte_start')
            b_end = n.get('byte_end')
            if b_start is None or b_end is None:
                continue # Salta nodi malformati

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

    @staticmethod
    def adapt_relations(db_edges: List[Dict], nodes_map: Dict[str, Any]) -> List[Any]:
        res = []
        for e in db_edges:
            src = nodes_map.get(e['source_id'])
            tgt = nodes_map.get(e['target_id'])
            
            # Risoluzione nomi file per visualizzazione
            s_file = src.file_path if src and src.file_path else "EXTERNAL"
            t_file = tgt.file_path if tgt and tgt.file_path else "EXTERNAL"
            
            # Fallback per metadati SCIP (external symbols)
            if not src and e.get('metadata', {}).get('is_external'): s_file = "EXTERNAL_LIB"
            if not tgt and e.get('metadata', {}).get('is_external'): t_file = "EXTERNAL_LIB"

            res.append(DbAdapter.RelView(
                source_id=e['source_id'],
                target_id=e['target_id'],
                relation_type=e['relation_type'],
                metadata=e['metadata'] if e['metadata'] else {},
                source_file=s_file,
                target_file=t_file
            ))
        return res

class HtmlGenerator:
    """Genera l'interfaccia HTML/JS."""
    
    TEMPLATE = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>Sheep Graph Debugger</title>
        <style>
            :root { 
                --bg: #1e1e1e; --panel: #252526; --text: #cccccc; 
                --accent: #0e639c; --border: #3e3e42; 
                --scip: #4fc1ff; --ts: #6a9955; --unk: #888;
            }
            body { margin:0; font-family: 'Segoe UI', sans-serif; background: var(--bg); color: var(--text); height: 100vh; display: flex; flex-direction: column; overflow: hidden; }
            
            #header { height: 40px; background: #333; display: flex; align-items: center; padding: 0 15px; border-bottom: 1px solid #000; flex-shrink: 0; }
            #header h1 { font-size: 16px; margin: 0; color: #fff; font-weight: 600; letter-spacing: 0.5px; }
            #header .meta { margin-left: 20px; font-size: 12px; color: #aaa; font-family: monospace; }
            button#rerun-btn { margin-left: auto; background: var(--accent); border: none; color: white; padding: 5px 15px; cursor: pointer; font-size: 12px; font-weight: bold; border-radius: 2px; }
            button#rerun-btn:hover { filter: brightness(1.2); }

            #layout { flex: 1; display: flex; overflow: hidden; }
            .col { display: flex; flex-direction: column; border-right: 1px solid var(--border); }
            .col-header { padding: 8px 12px; background: var(--panel); font-weight: bold; font-size: 11px; text-transform: uppercase; color: #fff; border-bottom: 1px solid var(--border); }
            .col-body { flex: 1; overflow-y: auto; position: relative; }

            #code-view { font-family: 'Consolas', monospace; font-size: 13px; line-height: 1.5; white-space: pre-wrap; padding: 20px; color: #d4d4d4; }
            .chunk { cursor: pointer; border-radius: 2px; transition: background 0.1s; }
            .chunk:hover { background: rgba(255, 255, 255, 0.05); outline: 1px solid #555; }
            .chunk.selected { background: rgba(38, 79, 120, 0.5) !important; outline: 1px solid var(--scip); box-shadow: 0 0 10px rgba(0,0,0,0.5); z-index: 10; position: relative; }
            .chunk.type-class { background: rgba(78, 201, 176, 0.15); }
            .chunk.type-func { background: rgba(220, 220, 170, 0.15); }

            .list-item { padding: 6px 10px; border-bottom: 1px solid #303031; font-size: 12px; cursor: pointer; display: flex; align-items: center; }
            .list-item:hover { background: #2a2d2e; }
            .list-item.active { background: #094771; color: white; }
            .badge { font-size: 9px; padding: 2px 5px; border-radius: 3px; margin-right: 8px; min-width: 50px; text-align: center; font-weight: bold; color: #1e1e1e; text-transform: uppercase; }
            
            .rel-group-title { font-size: 10px; font-weight: bold; color: #888; padding: 5px 10px; background: #222; letter-spacing: 1px; }
            .rel-row { padding: 8px 10px; border-bottom: 1px solid #333; display: flex; flex-direction: column; cursor: pointer; border-left: 3px solid transparent; }
            .rel-row:hover { background: #2d2d30; }
            .rel-row.rel-ts { border-left-color: var(--ts); }
            .rel-row.rel-scip { border-left-color: var(--scip); }
            
            .rel-main { display: flex; align-items: center; justify-content: space-between; }
            .rel-type { font-weight: bold; font-size: 12px; color: #ccc; }
            .rel-tool { font-size: 9px; padding: 1px 4px; border-radius: 2px; margin-left: 8px; font-weight: bold; color: #fff; }
            .bg-scip { background: var(--scip); color: #000; }
            .bg-ts { background: var(--ts); color: #000; }
            
            .rel-target { font-family: monospace; font-size: 11px; color: #aaa; margin-top: 4px; word-break: break-all; }
            
            .meta-table { margin-top: 6px; font-size: 10px; background: #181818; padding: 4px; border-radius: 3px; display: none; }
            .rel-row:hover .meta-table { display: block; }
            .meta-kv { display: flex; margin-bottom: 2px; }
            .meta-k { color: #569cd6; width: 70px; flex-shrink: 0; }
            .meta-v { color: #ce9178; word-break: break-all; }

            #info-box { padding: 15px; border-bottom: 1px solid var(--border); background: #222; }
            #info-id { font-family: monospace; font-size: 11px; color: #fff; margin-bottom: 5px; word-break: break-all; }
            #info-type { font-size: 11px; color: var(--scip); font-weight: bold; text-transform: uppercase; }

            ::-webkit-scrollbar { width: 10px; background: #1e1e1e; }
            ::-webkit-scrollbar-thumb { background: #424242; border-radius: 5px; border: 2px solid #1e1e1e; }
        </style>
    </head>
    <body>
        <div id="header">
            <h1>🐑 Visual Debugger</h1>
            <span class="meta" id="file-path">Loading...</span>
            <button id="rerun-btn" onclick="rerun()">RE-INDEX</button>
        </div>
        <div id="layout">
            <div class="col" style="flex: 5;">
                <div class="col-header">Source Code</div>
                <div class="col-body" id="code-view"></div>
            </div>
            <div class="col" style="flex: 2; min-width: 250px;">
                <div class="col-header">Chunks (<span id="chunk-count">0</span>)</div>
                <div class="col-body" id="chunk-list"></div>
            </div>
            <div class="col" style="flex: 3; min-width: 350px; border-right: none;">
                <div class="col-header">Node Details & Relations</div>
                <div class="col-body">
                    <div id="info-box" style="display:none;">
                        <div id="info-id"></div>
                        <div id="info-type"></div>
                    </div>
                    <div id="rel-list">
                        <div style="padding: 20px; text-align: center; color: #666; font-style: italic;">Select a chunk.</div>
                    </div>
                </div>
            </div>
        </div>

        <script>
            const DATA = __DATA_JSON__;
            
            window.onload = function() {
                document.getElementById('file-path').innerText = DATA.file;
                document.getElementById('chunk-count').innerText = DATA.nodes.length;
                renderCode();
                renderList();
            };

            function rerun() {
                const btn = document.getElementById('rerun-btn');
                btn.disabled = true; btn.innerText = "INDEXING...";
                fetch('/rerun').then(() => location.reload()).catch(() => btn.innerText = "ERROR");
            }

            function renderCode() {
                document.getElementById('code-view').innerHTML = DATA.html;
            }

            function renderList() {
                const list = document.getElementById('chunk-list');
                list.innerHTML = '';
                DATA.nodes.forEach(n => {
                    const div = document.createElement('div');
                    div.className = 'list-item';
                    div.id = 'li-' + n.id;
                    div.onclick = () => selectChunk(n.id);
                    
                    let color = '#888';
                    if (n.type.includes('function')) color = '#dcdcaa';
                    else if (n.type.includes('class')) color = '#4ec9b0';
                    else if (n.type.includes('signature')) color = '#c586c0';
                    
                    div.innerHTML = `<span class="badge" style="background:${color}">${n.type.substring(0,4)}</span><span style="font-family:monospace; overflow:hidden; text-overflow:ellipsis;">${n.id.substring(0,8)}...</span>`;
                    list.appendChild(div);
                });
            }

            function selectChunk(id) {
                document.querySelectorAll('.selected, .active').forEach(e => e.classList.remove('selected', 'active'));
                const chunkEl = document.getElementById('chunk-' + id);
                const liEl = document.getElementById('li-' + id);
                if(chunkEl) { chunkEl.classList.add('selected'); chunkEl.scrollIntoView({block: 'center', behavior: 'smooth'}); }
                if(liEl) { liEl.classList.add('active'); liEl.scrollIntoView({block: 'center', behavior: 'smooth'}); }

                const node = DATA.nodes.find(n => n.id === id);
                if(!node) return;
                
                document.getElementById('info-box').style.display = 'block';
                document.getElementById('info-id').innerText = node.id;
                document.getElementById('info-type').innerText = node.type;
                renderRelations(id);
            }

            function renderRelations(id) {
                const container = document.getElementById('rel-list');
                const rels = DATA.relations[id] || { incoming: [], outgoing: [] };
                container.innerHTML = '';

                if (rels.incoming.length === 0 && rels.outgoing.length === 0) {
                    container.innerHTML = '<div style="padding:20px; text-align:center; color:#555;">No relations found.</div>';
                    return;
                }

                const createRelRow = (r, dir) => {
                    const meta = r.metadata || {};
                    const tool = meta.tool || 'unknown';
                    const isScip = tool.includes('scip');
                    const toolLabel = isScip ? 'SCIP' : (tool.includes('treesitter') ? 'TREE-SITTER' : 'UNK');
                    const badgeClass = isScip ? 'bg-scip' : (tool.includes('treesitter') ? 'bg-ts' : 'bg-unk');
                    const rowClass = isScip ? 'rel-scip' : 'rel-ts';
                    
                    let metaHtml = '';
                    for (const [k, v] of Object.entries(meta)) {
                        if (k !== 'tool') metaHtml += `<div class="meta-kv"><div class="meta-k">${k}</div><div class="meta-v">${v}</div></div>`;
                    }

                    const targetLabel = dir === 'out' ? 
                        `-> ${r.target_file} : ${r.target_id ? r.target_id.substring(0,8)+'...' : '?'}` : 
                        `<- ${r.source_file} : ${r.source_id ? r.source_id.substring(0,8)+'...' : '?'}`;

                    return `
                        <div class="rel-row ${rowClass}" onclick="selectChunk('${dir==='out' ? r.target_id : r.source_id}')">
                            <div class="rel-main">
                                <span class="rel-type">${r.relation_type.toUpperCase()}</span>
                                <span class="rel-tool ${badgeClass}">${toolLabel}</span>
                            </div>
                            <div class="rel-target">${targetLabel}</div>
                            ${metaHtml ? `<div class="meta-table">${metaHtml}</div>` : ''}
                        </div>`;
                };

                if (rels.outgoing.length > 0) {
                    container.innerHTML += '<div class="rel-group-title">OUTGOING (This -> Other)</div>';
                    rels.outgoing.forEach(r => container.innerHTML += createRelRow(r, 'out'));
                }
                if (rels.incoming.length > 0) {
                    container.innerHTML += '<div class="rel-group-title">INCOMING (Other -> This)</div>';
                    rels.incoming.forEach(r => container.innerHTML += createRelRow(r, 'in'));
                }
            }
            window.selectChunk = selectChunk;
        </script>
    </body>
    </html>
    """

    @staticmethod
    def prepare_payload(target_file_abs: str, repo_root: str, nodes: List[Any], contents: Dict[str, Any], relations: List[Any]) -> str:
        # --- FIX CRITICO: Filtro rigoroso per Path Relativo ---
        # Calcoliamo il path relativo esatto come lo calcola il Parser
        target_rel_path = os.path.relpath(target_file_abs, repo_root)
        
        # Filtriamo i nodi che hanno ESATTAMENTE questo path relativo
        file_nodes = [n for n in nodes if n.file_path == target_rel_path]
        file_nodes.sort(key=lambda x: x.byte_range[0])
        
        # Mappa Relazioni
        rel_map = {n.id: {"incoming": [], "outgoing": []} for n in file_nodes}
        node_ids = set(n.id for n in file_nodes)

        for rel in relations:
            if rel.source_id in node_ids:
                rel_map[rel.source_id]["outgoing"].append(rel)
            if rel.target_id in node_ids:
                rel_map[rel.target_id]["incoming"].append(rel)

        # HTML Code Generation
        try:
            with open(target_file_abs, 'rb') as f: source_bytes = f.read()
        except Exception: source_bytes = b"[File not found]"

        events = []
        for n in file_nodes:
            events.append((n.byte_range[0], 1, n))  # Start
            events.append((n.byte_range[1], -1, n)) # End
        
        # Ordinamento robusto per nesting
        def sort_key(evt):
            pos, type, node = evt
            length = node.byte_range[1] - node.byte_range[0]
            # 1. End (-1) prima di Start (1) alla stessa posizione
            type_rank = 0 if type == -1 else 1
            # 2. Per Start: più lunghi (outer) prima. Per End: più corti (inner) prima.
            len_rank = -length if type == 1 else length
            return (pos, type_rank, len_rank)

        events.sort(key=sort_key)

        html_parts = []
        last_idx = 0
        nodes_json = []

        for idx, type, node in events:
            if idx > last_idx:
                segment = source_bytes[last_idx:idx].decode('utf-8', errors='replace')
                html_parts.append(html.escape(segment))
            
            if type == 1: # Start
                cls = "chunk"
                if "class" in node.type: cls += " type-class"
                elif "function" in node.type or "method" in node.type: cls += " type-func"
                
                html_parts.append(f'<span id="chunk-{node.id}" class="{cls}" onclick="selectChunk(\'{node.id}\'); event.stopPropagation();">')
                nodes_json.append({"id": node.id, "type": node.type})
            else: # End
                html_parts.append('</span>')
            last_idx = idx

        if last_idx < len(source_bytes):
            html_parts.append(html.escape(source_bytes[last_idx:].decode('utf-8', errors='replace')))

        import dataclasses
        rel_map_serializable = {}
        for nid, rels in rel_map.items():
            rel_map_serializable[nid] = {
                "incoming": [dataclasses.asdict(r) for r in rels["incoming"]],
                "outgoing": [dataclasses.asdict(r) for r in rels["outgoing"]]
            }
        
        data_payload = {
            "file": target_rel_path,
            "html": "".join(html_parts),
            "nodes": nodes_json,
            "relations": rel_map_serializable
        }
        return json.dumps(data_payload)

class DebugHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urlparse(self.path)
        
        if parsed.path == '/':
            self.send_response(200)
            self.send_header('Content-type', 'text/html')
            self.end_headers()
            
            print(f"🔄 Indexing Repo: {repo_root_abs}...")
            indexer = CodebaseIndexer(repo_root_abs)
            indexer.index()
            
            raw_nodes = list(indexer.get_nodes())
            raw_rels = list(indexer.get_edges())
            
            nodes = DbAdapter.adapt_nodes(raw_nodes)
            nodes_map = {n.id: n for n in nodes}
            rels = DbAdapter.adapt_relations(raw_rels, nodes_map)
            
            # Passiamo target_file_abs E repo_root_abs per il calcolo corretto
            json_data = HtmlGenerator.prepare_payload(target_file_abs, repo_root_abs, nodes, {}, rels)
            page = HtmlGenerator.TEMPLATE.replace('__DATA_JSON__', json_data)
            
            self.wfile.write(page.encode('utf-8'))
            indexer.close()
            print("✅ Page Served.")

        elif parsed.path == '/rerun':
            self.send_response(200); self.end_headers(); self.wfile.write(b"OK")
        else:
            self.send_error(404)

def main():
    global target_file_abs, repo_root_abs
    parser = argparse.ArgumentParser(description="Sheep Visual Debugger")
    parser.add_argument("file", help="Target file to debug")
    args = parser.parse_args()

    target_file_abs = os.path.abspath(args.file)
    if not os.path.isfile(target_file_abs):
        print(f"❌ File non trovato: {target_file_abs}")
        sys.exit(1)
        
    repo_root_abs = os.path.dirname(target_file_abs)
    curr = repo_root_abs
    while len(curr) > 1:
        if os.path.exists(os.path.join(curr, ".git")):
            repo_root_abs = curr
            break
        curr = os.path.dirname(curr)

    print(f"\n🐑 SHEEP VISUAL DEBUGGER")
    print(f"   File: {target_file_abs}")
    print(f"   Repo: {repo_root_abs}")
    print(f"   URL:  http://localhost:{SERVER_PORT}")
    
    try:
        webbrowser.open(f"http://localhost:{SERVER_PORT}")
        HTTPServer(('localhost', SERVER_PORT), DebugHandler).serve_forever()
    except KeyboardInterrupt:
        print("\n🛑 Server stopped.")

if __name__ == "__main__":
    main()