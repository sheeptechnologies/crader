const API_BASE = '/api';

const repoPathInput = document.getElementById('repoPath');
const indexBtn = document.getElementById('indexBtn');
const statusSpan = document.getElementById('status');
const filesContainer = document.getElementById('filesContainer');
const codeContainer = document.getElementById('codeContainer');
const currentFileHeader = document.getElementById('currentFile');
const codeView = document.getElementById('codeView');
const graphView = document.getElementById('graphView');
const closeGraphBtn = document.getElementById('closeGraphBtn');
const networkContainer = document.getElementById('network');
const nodeDetails = document.getElementById('nodeDetails');

let network = null;
let currentGraphData = null;

if (typeof vis === 'undefined') {
    alert('CRITICAL: vis-network library not loaded.');
}

// Version indicator
statusSpan.textContent = "Ready (v2.0)";

window.selectChunk = function (chunkId, event) {
    if (event) event.stopPropagation();
    openGraph(chunkId);
};

indexBtn.addEventListener('click', async () => {
    const path = repoPathInput.value;
    if (!path) return;
    statusSpan.textContent = 'Indexing...';
    indexBtn.disabled = true;
    try {
        const res = await fetch(`${API_BASE}/index`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ repo_path: path })
        });
        if (!res.ok) throw new Error(await res.text());
        const data = await res.json();
        statusSpan.textContent = `Indexed! Files: ${data.stats.files}, Nodes: ${data.stats.total_nodes}`;
        loadFiles();
    } catch (e) {
        statusSpan.textContent = `Error: ${e.message}`;
        console.error(e);
    } finally {
        indexBtn.disabled = false;
    }
});

async function loadFiles() {
    try {
        const res = await fetch(`${API_BASE}/files`);
        if (!res.ok) throw new Error(await res.text());
        const data = await res.json();
        renderFileList(data.files);
    } catch (e) {
        console.error("Failed to load files", e);
    }
}

const filesListView = document.getElementById('filesListView');
const fileDetailsView = document.getElementById('fileDetailsView');
const fileDetailsContent = document.getElementById('fileDetailsContent');
const backToFilesBtn = document.getElementById('backToFilesBtn');

backToFilesBtn.addEventListener('click', () => {
    fileDetailsView.classList.add('hidden');
    filesListView.classList.remove('hidden');
});

function renderFileList(files) {
    filesContainer.innerHTML = '';
    files.forEach(file => {
        const fileDiv = document.createElement('div');
        fileDiv.className = 'file-item';
        fileDiv.textContent = file.path.split('/').pop();
        fileDiv.title = file.path;
        fileDiv.addEventListener('click', () => {
            document.querySelectorAll('.file-item').forEach(el => el.classList.remove('active'));
            fileDiv.classList.add('active');

            // Load code in center
            loadFileView(file.path);

            // Show details in left panel
            showFileDetails(file);
        });
        filesContainer.appendChild(fileDiv);
    });
}

function formatBytes(bytes, decimals = 2) {
    if (!+bytes) return '0 Bytes';
    const k = 1024;
    const dm = decimals < 0 ? 0 : decimals;
    const sizes = ['Bytes', 'KB', 'MB', 'GB', 'TB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return `${parseFloat((bytes / Math.pow(k, i)).toFixed(dm))} ${sizes[i]}`;
}

function showFileDetails(file) {
    filesListView.classList.add('hidden');
    fileDetailsView.classList.remove('hidden');

    const rows = [
        { label: "Path", value: file.path },
        { label: "Language", value: file.language },
        { label: "Size", value: formatBytes(file.size_bytes) },
        { label: "Category", value: file.category },
        { label: "Indexed At", value: new Date(file.indexed_at).toLocaleString() },
        { label: "File Hash", value: file.file_hash, mono: true },
        { label: "Commit", value: file.commit_hash, mono: true },
        { label: "ID", value: file.id, mono: true },
    ];

    let html = `<div style="display:flex; flex-direction:column; gap:12px;">`;

    rows.forEach(row => {
        if (!row.value) return;
        html += `
            <div style="background:#2d2d30; padding:8px; border-radius:4px; border-left:3px solid #007acc;">
                <div style="color:#858585; font-size:10px; text-transform:uppercase; margin-bottom:4px;">${row.label}</div>
                <div style="color:#cccccc; font-size:12px; word-break:break-all; ${row.mono ? 'font-family:Consolas, monospace;' : ''}">
                    ${escapeHtml(String(row.value))}
                </div>
            </div>
        `;
    });

    html += `</div>`;
    fileDetailsContent.innerHTML = html;
}

async function loadFileView(path) {
    currentFileHeader.textContent = path.split('/').pop();
    codeContainer.innerHTML = 'Loading...';
    codeView.classList.remove('hidden');
    graphView.classList.add('hidden');

    try {
        const res = await fetch(`${API_BASE}/file_view?path=${encodeURIComponent(path)}`);
        if (!res.ok) throw new Error(await res.text());
        const data = await res.json();
        codeContainer.innerHTML = data.html;
    } catch (e) {
        codeContainer.textContent = `Error loading content: ${e.message}`;
    }
}

async function openGraph(chunkId) {
    codeView.classList.add('hidden');
    graphView.classList.remove('hidden');

    nodeDetails.innerHTML = '<div style="padding:15px; color:#858585;">Loading graph data...</div>';

    try {
        const res = await fetch(`${API_BASE}/chunk/${chunkId}/graph`);
        if (!res.ok) throw new Error(await res.text());
        const data = await res.json();
        currentGraphData = data;

        renderGraph(data, chunkId);

        const centerNode = data.nodes.find(n => n.id === chunkId);
        if (centerNode) {
            updateDetailsPanel(centerNode, data.edges, data.nodes);
        }
    } catch (e) {
        console.error('Graph error:', e);
        nodeDetails.textContent = `Error loading graph: ${e.message}`;
    }
}

function createNodeSvg(node, isSelected) {
    const width = 800;
    const lineHeight = 20;
    const maxLines = 50;

    let codeLines = [];
    if (node.content) {
        const lines = node.content.split('\n');
        if (lines.length > maxLines) {
            codeLines = lines.slice(0, maxLines);
            codeLines.push("... (truncated)");
        } else {
            codeLines = lines;
        }
    } else {
        codeLines = ["(No content)"];
    }

    const headerHeight = 40;
    const padding = 16;
    const contentHeight = codeLines.length * lineHeight + (padding * 2);
    const totalHeight = headerHeight + contentHeight;

    // Colors
    const borderColor = isSelected ? '#007acc' : '#3e3e42';
    const borderWidth = isSelected ? 3 : 1;
    const bgColor = '#1e1e1e';
    const headerBg = isSelected ? '#007acc' : '#252526';
    const headerText = isSelected ? '#ffffff' : '#cccccc';
    const textColor = '#d4d4d4';
    const fontFamily = "Consolas, Monaco, 'Courier New', monospace";

    // Escape XML entities for text
    const escapeXml = (str) => {
        return str.replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&apos;');
    };

    // Generate code text elements
    const codeSvgLines = codeLines.map((line, i) => {
        const y = headerHeight + padding + (i * lineHeight) + 14; // +14 for baseline approximation
        // Simple syntax highlighting simulation (coloring comments or keywords roughly)
        let color = textColor;
        const trimmed = line.trim();
        if (trimmed.startsWith('#') || trimmed.startsWith('//')) color = '#6a9955'; // Comment
        else if (trimmed.startsWith('def ') || trimmed.startsWith('class ')) color = '#569cd6'; // Keyword
        else if (trimmed.startsWith('import ') || trimmed.startsWith('from ')) color = '#c586c0'; // Import

        return `<text x="${padding}" y="${y}" fill="${color}" font-family="${fontFamily}" font-size="14" xml:space="preserve">${escapeXml(line)}</text>`;
    }).join('');

    const svg = `
    <svg xmlns="http://www.w3.org/2000/svg" width="${width}" height="${totalHeight}">
      <defs>
        <filter id="shadow" x="-20%" y="-20%" width="140%" height="140%">
          <feGaussianBlur in="SourceAlpha" stdDeviation="4"/>
          <feOffset dx="0" dy="4" result="offsetblur"/>
          <feComponentTransfer>
            <feFuncA type="linear" slope="0.5"/>
          </feComponentTransfer>
          <feMerge>
            <feMergeNode/>
            <feMergeNode in="SourceGraphic"/>
          </feMerge>
        </filter>
      </defs>
      
      <g filter="url(#shadow)">
        <!-- Main Background -->
        <rect x="2" y="2" width="${width - 4}" height="${totalHeight - 4}" rx="6" ry="6" fill="${bgColor}" stroke="${borderColor}" stroke-width="${borderWidth}"/>
        
        <!-- Header Background -->
        <path d="M 2 2 L ${width - 2} 2 Q ${width - 2} 2 ${width - 2} 8 L ${width - 2} ${headerHeight} L 2 ${headerHeight} L 2 8 Q 2 2 2 2 Z" fill="${headerBg}"/>
        
        <!-- Header Text -->
        <text x="16" y="26" fill="${headerText}" font-family="Segoe UI, sans-serif" font-size="14" font-weight="bold">
            📄 ${escapeXml(node.file_path.split('/').pop())}
        </text>
        <text x="${width - 16}" y="26" fill="${headerText}" font-family="${fontFamily}" font-size="12" text-anchor="end" opacity="0.8">
            L${node.start_line}-${node.end_line}
        </text>
        
        <!-- Divider -->
        <line x1="2" y1="${headerHeight}" x2="${width - 2}" y2="${headerHeight}" stroke="${borderColor}" stroke-width="1"/>
        
        <!-- Code Content -->
        ${codeSvgLines}
      </g>
    </svg>
    `;

    // Use standard encoding for pure SVG, it's safer than base64 for simple text
    return "data:image/svg+xml;charset=utf-8," + encodeURIComponent(svg);
}

function renderGraph(data, centerId) {
    const nodes = new vis.DataSet(data.nodes.map(n => ({
        id: n.id,
        image: createNodeSvg(n, n.id === centerId),
        shape: 'image',
        shapeProperties: { useImageSize: true }
    })));

    const edgeGroups = new Map();
    data.edges.forEach(e => {
        const key = `${e.source_id}->${e.target_id}`;
        if (!edgeGroups.has(key)) edgeGroups.set(key, []);
        edgeGroups.get(key).push(e);
    });

    const visualEdges = [];
    edgeGroups.forEach((rels, key) => {
        const first = rels[0];
        visualEdges.push({
            from: first.source_id,
            to: first.target_id,
            arrows: 'to',
            color: { color: '#585858', opacity: 0.8 },
            width: 1.5,
            smooth: { type: 'cubicBezier', forceDirection: 'horizontal', roundness: 0.4 }
        });
    });

    const edges = new vis.DataSet(visualEdges);
    const container = document.getElementById('network');
    const graphData = { nodes, edges };

    const options = {
        height: '100%',
        width: '100%',
        layout: {
            hierarchical: false
        },
        physics: {
            enabled: true,
            solver: 'barnesHut',
            barnesHut: {
                gravitationalConstant: -200000, // Extremely strong repulsion
                centralGravity: 0.1,
                springLength: 800, // Much longer springs
                springConstant: 0.005, // Softer springs
                damping: 0.3,
                avoidOverlap: 1
            },
            stabilization: {
                enabled: true,
                iterations: 2000,
                updateInterval: 50,
                onlyDynamicEdges: false,
                fit: true
            }
        },
        interaction: {
            hover: true,
            dragNodes: true,
            zoomView: true,
            dragView: true,
            multiselect: false
        }
    };

    if (network) network.destroy();
    network = new vis.Network(container, graphData, options);

    // CRITICAL: Disable physics after stabilization to prevent "spring back"
    network.once("stabilizationIterationsDone", function () {
        network.setOptions({ physics: { enabled: false } });
        network.fit({
            animation: {
                duration: 500,
                easingFunction: 'easeInOutQuad'
            }
        });
    });

    network.on("click", function (params) {
        if (params.nodes.length > 0) {
            const nodeId = params.nodes[0];
            const node = data.nodes.find(n => n.id === nodeId);
            updateDetailsPanel(node, data.edges, data.nodes);
        }
    });

    // Re-enable physics temporarily on drag start? No, user wants manual placement.
    // If we re-enable, it will spring back. So we keep it disabled.
    // vis-network allows dragging nodes even with physics disabled.
}

function updateDetailsPanel(node, allEdges, allNodes) {
    const connections = [];
    allEdges.forEach(e => {
        if (e.source_id === node.id) {
            const target = allNodes.find(n => n.id === e.target_id);
            connections.push({ dir: 'OUT', type: e.relation_type, other: target, meta: e.metadata });
        } else if (e.target_id === node.id) {
            const source = allNodes.find(n => n.id === e.source_id);
            connections.push({ dir: 'IN', type: e.relation_type, other: source, meta: e.metadata });
        }
    });

    let html = `<div style="padding: 15px;">`;
    html += `<div style="margin-bottom:15px; padding-bottom:10px; border-bottom:1px solid #3e3e42;">`;
    html += `<h3 style="margin:0 0 5px 0; color: #fff; font-size:14px; font-weight:600;">${node.type}</h3>`;
    html += `<div style="font-family:'Consolas', monospace; color:#858585; font-size:12px;">${node.file_path}:${node.start_line}</div>`;
    html += `</div>`;

    // Node Details Section
    const nodeData = { ...node };
    delete nodeData.content; // Don't show full content in details
    const nodeJson = JSON.stringify(nodeData, null, 2);

    html += `<h4 style="color:#cccccc; margin:15px 0 10px 0; font-size:12px; text-transform:uppercase;">Chunk Data</h4>`;
    html += `<pre style="margin:0; padding:10px; background:#1e1e1e; color:#9cdcfe; font-size:11px; overflow:auto; border-radius:4px; border:1px solid #3e3e42; max-height:200px;">${escapeHtml(nodeJson)}</pre>`;

    html += `<h4 style="color:#cccccc; margin:15px 0 10px 0; font-size:12px; text-transform:uppercase;">Connections (${connections.length})</h4>`;
    html += `<div style="display:flex; flex-direction:column; gap:8px;">`;

    connections.forEach(c => {
        const color = c.dir === 'OUT' ? '#007acc' : '#d7ba7d';
        const arrow = c.dir === 'OUT' ? '➔' : '⬅';
        const otherName = c.other ? (c.other.file_path.split('/').pop() + ':' + c.other.start_line) : 'Unknown';
        const otherType = c.other ? c.other.type : 'External';

        html += `<div style="background:#2d2d30; padding:8px; border-radius:3px; border-left:3px solid ${color};">`;
        html += `<div style="display:flex; justify-content:space-between; margin-bottom:4px;">`;
        html += `<span style="color:${color}; font-weight:bold; font-size:11px;">${arrow} ${c.type.toUpperCase()}</span>`;
        html += `<span style="color:#858585; font-size:10px;">${otherType}</span>`;
        html += `</div>`;
        html += `<div style="color:#cccccc; font-size:12px; font-family:'Consolas', monospace; overflow:hidden; text-overflow:ellipsis; margin-bottom:4px;">${otherName}</div>`;

        // JSON Metadata
        if (c.meta && Object.keys(c.meta).length > 0) {
            const jsonStr = JSON.stringify(c.meta, null, 2);
            html += `<pre style="margin:0; padding:4px; background:#1e1e1e; color:#9cdcfe; font-size:10px; overflow:auto; border-radius:2px;">${escapeHtml(jsonStr)}</pre>`;
        }

        html += `</div>`;
    });

    html += `</div></div>`;
    nodeDetails.innerHTML = html;
}

function escapeHtml(text) {
    return text.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#039;");
}

closeGraphBtn.addEventListener('click', () => {
    graphView.classList.add('hidden');
    codeView.classList.remove('hidden');
});
