import os
import sys
import json
import argparse
import html
import webbrowser
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse
from typing import List, Dict, Any

# --- CONFIG PATH ---
current_dir = os.path.dirname(os.path.abspath(__file__))
src_dir = os.path.abspath(os.path.join(current_dir, '..', 'src'))
if src_dir not in sys.path:
    sys.path.insert(0, src_dir)

try:
    from crader.parsing.parser import TreeSitterRepoParser
    from crader.models import ChunkNode, ParsingResult
except ImportError as e:
    print(f"[FATAL] Errore importazione: {e}")
    sys.exit(1)

# --- GLOBAL STATE ---
SERVER_PORT = 8000
current_target_file = ""
current_repo_root = ""

class HtmlVisualizer:
    """Genera il contenuto HTML per la UI."""
    
    TEMPLATE = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>Sheep Parser - Live Debugger</title>
        <style>
            /* --- RESET & BASE --- */
            :root { 
                --bg: #1e1e1e; --panel-bg: #252526; --text: #d4d4d4; 
                --accent: #007acc; --border: #3e3e42; --highlight: #264F78; 
            }
            * { box-sizing: border-box; }
            body { margin: 0; font-family: 'Segoe UI', sans-serif; background: var(--bg); color: var(--text); height: 100vh; width: 100vw; overflow: hidden; display: flex; flex-direction: column; }
            
            /* --- TOOLBAR --- */
            #toolbar { height: 50px; flex-shrink: 0; background: #333; display: flex; align-items: center; padding: 0 15px; border-bottom: 1px solid #000; box-shadow: 0 2px 5px rgba(0,0,0,0.2); z-index: 100; }
            button { background: var(--accent); color: white; border: none; padding: 8px 16px; border-radius: 3px; cursor: pointer; font-weight: bold; transition: 0.2s; font-size: 13px; }
            button:hover { filter: brightness(1.1); }
            button:disabled { filter: grayscale(1); cursor: not-allowed; }
            .status { margin-left: auto; font-size: 0.85em; color: #aaa; font-family: monospace;}

            /* --- LAYOUT --- */
            #main-container { flex: 1; display: flex; flex-direction: row; overflow: hidden; min-height: 0; }
            .panel { display: flex; flex-direction: column; border-right: 1px solid var(--border); min-width: 0; height: 100%; }
            #panel-code { flex: 5; } 
            #panel-list { flex: 2; min-width: 250px; }
            #panel-rels { flex: 2; min-width: 280px; border-right: none; }
            .panel-header { padding: 10px; background: var(--panel-bg); font-weight: bold; border-bottom: 1px solid var(--border); font-size: 0.85em; text-transform: uppercase; letter-spacing: 1px; flex-shrink: 0; }
            .panel-content { flex: 1; overflow-y: auto; padding: 0; position: relative; }

            /* --- CODE VIEW --- */
            #code-content { padding: 20px; white-space: pre-wrap; font-family: 'Consolas', 'Monaco', monospace; line-height: 1.5; font-size: 13px; }
            .code-chunk { border-radius: 2px; cursor: pointer; transition: background 0.1s; }
            .code-chunk:hover { outline: 1px solid rgba(255,255,255,0.4); z-index: 10; position: relative;}
            .code-chunk.active { outline: 2px solid gold; background-color: rgba(255, 255, 0, 0.15) !important; z-index: 20; position: relative; box-shadow: 0 4px 12px rgba(0,0,0,0.5); }

            /* --- LIST VIEW --- */
            .list-item { padding: 8px 12px; cursor: pointer; border-bottom: 1px solid #333; font-size: 12px; display: flex; align-items: center; transition: background 0.1s; }
            .list-item:hover { background: #2a2d2e; }
            .list-item.active { background: var(--highlight); color: white; border-left: 4px solid var(--accent); }
            .badge { padding: 2px 6px; border-radius: 3px; font-size: 10px; margin-right: 10px; width: 60px; text-align: center; font-weight: bold; color: #1e1e1e; flex-shrink: 0; text-transform: uppercase; }

            /* --- RELATIONS --- */
            .rel-title { color: #888; font-size: 0.75em; margin-bottom: 8px; display: block; border-bottom: 1px solid #444; padding-bottom: 2px; letter-spacing: 1px; margin-top: 15px; }
            .rel-item { display: flex; align-items: center; padding: 8px; background: #333; margin-bottom: 5px; border-radius: 4px; cursor: pointer; font-size: 12px; border: 1px solid transparent; }
            .rel-item:hover { background: #444; border-color: #555; }
            
            ::-webkit-scrollbar { width: 10px; height: 10px; }
            ::-webkit-scrollbar-track { background: #1e1e1e; }
            ::-webkit-scrollbar-thumb { background: #444; border-radius: 5px; border: 2px solid #1e1e1e; }
            ::-webkit-scrollbar-thumb:hover { background: #555; }
        </style>
    </head>
    <body>
        <div id="toolbar">
            <span style="font-weight:bold; margin-right: 20px; font-size:1.1em">🐑 Sheep Visualizer</span>
            <button onclick="rerunParser()">⚡ RERUN PARSER</button>
            <span id="status-msg" class="status">Ready</span>
        </div>
        <div id="main-container">
            <div class="panel" id="panel-code">
                <div class="panel-header">Source Code</div>
                <div class="panel-content"><div id="code-content">__CODE_HTML__</div></div>
            </div>
            <div class="panel" id="panel-list">
                <div class="panel-header">Chunks (__CHUNK_COUNT__)</div>
                <div class="panel-content" id="chunk-list"></div>
            </div>
            <div class="panel" id="panel-rels">
                <div class="panel-header">Relations & Details</div>
                <div class="panel-content" id="rel-details">
                    <div style="padding:20px; color:#666; text-align:center; font-style:italic;">Seleziona un chunk.</div>
                </div>
            </div>
        </div>
        <script>
            const chunks = __CHUNKS_JSON__;
            const relations = __RELATIONS_JSON__; 

            window.onload = () => {
                renderList();
                document.getElementById('status-msg').innerText = "Loaded " + chunks.length + " chunks.";
            };

            function rerunParser() {
                const btn = document.querySelector('button');
                btn.disabled = true; btn.innerText = "Running...";
                fetch('/rerun').then(() => location.reload()).catch(err => btn.innerText = "Error");
            }

            function renderList() {
                const container = document.getElementById('chunk-list');
                container.innerHTML = '';
                chunks.forEach(c => {
                    const div = document.createElement('div');
                    div.className = 'list-item'; div.id = 'li-' + c.id;
                    div.onclick = () => selectChunk(c.id);
                    let color = '#666'; let label = 'UNK';
                    if(c.type.includes('function') || c.type.includes('method')) { color = '#dcdcaa'; label = 'FUNC'; }
                    else if(c.type.includes('class')) { color = '#4ec9b0'; label = 'CLASS'; }
                    else if(c.type.includes('import')) { color = '#c586c0'; label = 'IMP'; }
                    else if(c.type.includes('comment')) { color = '#6a9955'; label = 'DOC'; }
                    else if(c.type.includes('code')) { color = '#569cd6'; label = 'CODE'; }
                    else if(c.type.includes('signature')) { color = '#c586c0'; label = 'SIG'; }
                    div.innerHTML = `<span class="badge" style="background:${color}">${label}</span><span style="font-family:monospace">${c.id.substring(0, 15)}...</span>`;
                    container.appendChild(div);
                });
            }

            function selectChunk(id) {
                document.querySelectorAll('.active').forEach(e => e.classList.remove('active'));
                const li = document.getElementById('li-' + id);
                if(li) { li.classList.add('active'); li.scrollIntoView({block: 'center', behavior: 'smooth'}); }
                const code = document.getElementById(id);
                if(code) { code.classList.add('active'); code.scrollIntoView({block: 'center', behavior: 'smooth'}); }
                renderRelations(id);
            }

            function renderRelations(id) {
                const container = document.getElementById('rel-details');
                const chunk = chunks.find(c => c.id === id);
                const rels = relations[id] || { parents: [], children: [], others: [] };
                if (!chunk) return;

                let html = `
                    <div style="padding:15px; background:#222; border-bottom:1px solid #444;">
                        <div style="font-size:10px; color:#888;">ID</div>
                        <div style="font-family:monospace; font-weight:bold; font-size:12px; color:#fff; margin-bottom:10px; word-break:break-all">${chunk.id}</div>
                        <div style="font-size:11px; color:#aaa">Type: ${chunk.type}</div>
                        <div style="font-size:11px; color:#aaa">Range: ${chunk.start_line}:${chunk.end_line}</div>
                    </div><div style="padding:10px;">`;

                const link = (tid, lbl, icon) => `<div class="rel-item" onclick="selectChunk('${tid}')"><div style="font-size:16px; margin-right:8px;">${icon}</div><div style="flex:1;"><div style="font-weight:bold; color:#ccc;">${lbl}</div><div style="font-family:monospace; color:#888;">${tid.substring(0,15)}...</div></div></div>`;

                if (rels.parents.length) { html += `<span class="rel-title">PARENT (Container)</span>` + rels.parents.map(p => link(p, 'Parent', '📦')).join(''); }
                if (rels.children.length) { html += `<span class="rel-title">CHILDREN (Contains)</span>` + rels.children.map(c => link(c, 'Child', '🔹')).join(''); }
                if (rels.others.length) { html += `<span class="rel-title">RELATED</span>` + rels.others.map(o => link(o.target, o.type, '🔗')).join(''); }
                
                html += `</div><div style="margin-top:10px; padding:15px; border-top:1px solid #333;"><span class="rel-title">CONTENT</span><pre style="background:#111; padding:10px; border-radius:4px; font-size:11px; color:#aaa; overflow:auto; max-height:300px; border:1px solid #333;">${escapeHtml(chunk.content)}</pre></div>`;
                container.innerHTML = html;
            }
            function escapeHtml(t) { return t ? t.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;") : ""; }
            document.querySelectorAll('.code-chunk').forEach(el => el.addEventListener('click', (e) => { e.stopPropagation(); selectChunk(el.id); }));
        </script>
    </body>
    </html>
    """

    @staticmethod
    def prepare_data(file_path: str, nodes: List[ChunkNode], contents: Dict[str, str], relations_list: List[Any] = None):
        current_nodes = [n for n in nodes if file_path.endswith(n.file_path)]
        current_nodes.sort(key=lambda x: x.byte_range[0])

        relations_map = {n.id: {"parents": [], "children": [], "others": []} for n in current_nodes}
        
        # 1. Aggiungi relazioni geometriche (Fallback)
        for child in current_nodes:
            for possible_parent in current_nodes:
                if child.id == possible_parent.id: continue
                if (possible_parent.byte_range[0] <= child.byte_range[0] and 
                    possible_parent.byte_range[1] >= child.byte_range[1]):
                    # Solo se non c'è già una relazione esplicita più forte
                    pass 

        # 2. Aggiungi relazioni ESPLICITE (Parser generated)
        # Queste vincono su tutto e permettono relazioni tra chunk disgiunti (Header Flow-Down)
        if relations_list:
            for rel in relations_list:
                src = getattr(rel, 'source_id', None)
                tgt = getattr(rel, 'target_id', None)
                rtype = getattr(rel, 'relation_type', 'related')
                
                # Se entrambi i nodi sono in questo file
                if src in relations_map and tgt in relations_map:
                    if rtype == 'child_of':
                        # Src è figlio di Tgt
                        if tgt not in relations_map[src]["parents"]:
                            relations_map[src]["parents"].append(tgt)
                        if src not in relations_map[tgt]["children"]:
                            relations_map[tgt]["children"].append(src)
                    else:
                        # Altre relazioni
                        relations_map[src]["others"].append({"target": tgt, "type": f"➡ {rtype}"})
                        relations_map[tgt]["others"].append({"target": src, "type": f"⬅ {rtype}"})

        try:
            with open(file_path, 'rb') as f: source_bytes = f.read()
        except: return "", "[]", "{}"

        events = []
        for n in current_nodes:
            events.append((n.byte_range[0], 1, n))  # Start
            events.append((n.byte_range[1], -1, n)) # End
        
        # Ordinamento eventi per annidamento corretto HTML
        # 1. Posizione
        # 2. Tipo: End (-1) prima di Start (1) a parità di posizione (chiudi tag prima di aprirne nuovi)
        # 3. Lunghezza: Start più lunghi prima (outer), End più corti prima (inner)
        def event_priority(evt):
            idx, type, node = evt
            length = node.byte_range[1] - node.byte_range[0]
            
            # End (-1) deve venire prima di Start (1)
            type_rank = 0 if type == -1 else 1
            
            # Per Start (1): Lunghezza decrescente (Outer first) => -length
            # Per End (-1): Lunghezza crescente (Inner first) => length
            len_rank = -length if type == 1 else length
            
            return (idx, type_rank, len_rank)

        events.sort(key=event_priority)

        html_buffer = []
        last_idx = 0
        chunk_json_list = []

        for idx, evt_type, node in events:
            if idx > last_idx:
                txt = source_bytes[last_idx:idx].decode('utf-8', errors='replace')
                html_buffer.append(html.escape(txt))
            
            if evt_type == 1:
                bg = "rgba(255,255,255,0.05)"
                if "class" in node.type: bg = "rgba(78, 201, 176, 0.1)"
                elif "function" in node.type: bg = "rgba(220, 220, 170, 0.1)"
                
                html_buffer.append(f'<span id="{node.id}" class="code-chunk" style="background:{bg}" title="{node.type}">')
                
                c_obj = contents.get(node.chunk_hash)
                c_txt = c_obj.content if hasattr(c_obj, 'content') else str(c_obj)
                chunk_json_list.append({
                    "id": node.id, "type": node.type, "hash": node.chunk_hash, "content": c_txt,
                    "start_line": node.start_line, "end_line": node.end_line, "byte_range": node.byte_range
                })
            else:
                html_buffer.append('</span>')
            last_idx = idx

        if last_idx < len(source_bytes):
            html_buffer.append(html.escape(source_bytes[last_idx:].decode('utf-8', errors='replace')))

        seen = set(); unique = []
        for x in chunk_json_list:
            if x['id'] not in seen: unique.append(x); seen.add(x['id'])

        return "".join(html_buffer), json.dumps(unique), json.dumps(relations_map)

class DebugServer(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == '/':
            self.send_response(200); self.send_header('Content-type', 'text/html'); self.end_headers()
            print(f"Running parser on: {current_repo_root}")
            parser = TreeSitterRepoParser(repo_path=current_repo_root)
            result = parser.extract_semantic_chunks()
            relations = getattr(result, 'relations', [])
            code_html, chunks_json, rels_json = HtmlVisualizer.prepare_data(current_target_file, result.nodes, result.contents, relations)
            out = HtmlVisualizer.TEMPLATE.replace('__CODE_HTML__', code_html).replace('__CHUNKS_JSON__', chunks_json).replace('__RELATIONS_JSON__', rels_json).replace('__CHUNK_COUNT__', str(len(json.loads(chunks_json))))
            self.wfile.write(out.encode('utf-8'))
        elif parsed.path == '/rerun':
            self.send_response(200); self.end_headers(); self.wfile.write(b"OK")
        else: self.send_response(404); self.end_headers()

def main():
    global current_target_file, current_repo_root
    parser = argparse.ArgumentParser()
    parser.add_argument("input_path", help="File python da analizzare")
    args = parser.parse_args()
    current_target_file = os.path.abspath(args.input_path)
    current_repo_root = os.path.dirname(current_target_file)
    print(f"🚀 Visual Debugger: http://localhost:{SERVER_PORT}")
    webbrowser.open(f"http://localhost:{SERVER_PORT}")
    HTTPServer(('localhost', SERVER_PORT), DebugServer).serve_forever()

if __name__ == "__main__":
    main()