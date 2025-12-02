const API_BASE = '/api';

const repoPathInput = document.getElementById('repoPath');
const indexBtn = document.getElementById('indexBtn');
const statusSpan = document.getElementById('status');
const filesContainer = document.getElementById('filesContainer');
const codeContainer = document.getElementById('codeContainer');
const currentFileHeader = document.getElementById('currentFile');
const codeView = document.getElementById('codeView');
const graphView = document.getElementById('graphView');
const navigatorView = document.getElementById('navigatorView');
const nodeDetails = document.getElementById('nodeDetails');

// New Views
const homeView = document.getElementById('homeView');
const mainContainer = document.getElementById('mainContainer');
const repoList = document.getElementById('repoList');
const searchView = document.getElementById('searchView');
const searchInput = document.getElementById('searchInput');
const searchBtn = document.getElementById('searchBtn');
const searchResults = document.getElementById('searchResults');
const searchStrategy = document.getElementById('searchStrategy');
const homeBtn = document.getElementById('homeBtn');

// Tabs
const contextTabs = document.getElementById('contextTabs');
const codeTabBtn = document.getElementById('codeTabBtn');
const graphTabBtn = document.getElementById('graphTabBtn');
const navigatorTabBtn = document.getElementById('navigatorTabBtn');
const searchTabBtn = document.getElementById('searchTabBtn');
const closeSearchBtn = document.getElementById('closeSearchBtn');

let network = null;
let currentGraphData = null;
let currentRepoId = null; // Track current repo context

if (typeof vis === 'undefined') {
    alert('CRITICAL: vis-network library not loaded.');
}

// Version indicator
statusSpan.textContent = "Ready (v2.1)";

// --- INITIALIZATION ---
loadRepositories();

window.selectChunk = function (chunkId, event) {
    if (event) event.stopPropagation();
    openGraph(chunkId);
};

// --- REPOSITORY MANAGEMENT ---

async function loadRepositories() {
    try {
        const res = await fetch(`${API_BASE}/repositories`);
        if (!res.ok) throw new Error(await res.text());
        const data = await res.json();
        renderRepoList(data.repositories);
    } catch (e) {
        console.error("Failed to load repositories", e);
        repoList.innerHTML = `<div style="color:red">Failed to load repositories: ${e.message}</div>`;
    }
}

function renderRepoList(groupedRepos) {
    repoList.innerHTML = '';
    if (groupedRepos.length === 0) {
        repoList.innerHTML = '<div style="color:#888; grid-column: 1/-1; text-align:center;">No repositories indexed yet. Use the controls above to index one.</div>';
        return;
    }

    groupedRepos.forEach(group => {
        const card = document.createElement('div');
        card.className = 'repo-card';

        // Create branch options
        let branchOptions = '';
        group.branches.forEach(b => {
            branchOptions += `<option value="${b.id}" data-name="${group.name}" data-branch="${b.branch}">
                ${escapeHtml(b.branch)} (${b.status}) - ${new Date(b.updated_at).toLocaleDateString()}
            </option>`;
        });

        const safeUrl = group.url || 'no-url';
        const safeId = safeUrl.replace(/[^a-zA-Z0-9]/g, '');

        card.innerHTML = `
            <h3>${escapeHtml(group.name || 'Unnamed Repo')}</h3>
            <div class="url">${escapeHtml(group.url || 'No URL')}</div>

            <div class="repo-controls">
                <select class="branch-select" id="select-${safeId}">
                    ${branchOptions}
                </select>
                <button class="select-repo-btn">Select</button>
            </div>
        `;

        const btn = card.querySelector('.select-repo-btn');
        const select = card.querySelector('select');

        btn.addEventListener('click', (e) => {
            e.stopPropagation();
            const selectedOption = select.options[select.selectedIndex];
            const repoId = select.value;
            const repoName = selectedOption.dataset.name;
            const repoBranch = selectedOption.dataset.branch;

            selectRepository({ id: repoId, name: repoName, branch: repoBranch });
        });

        repoList.appendChild(card);
    });
}

function selectRepository(repo) {
    currentRepoId = repo.id;
    homeView.classList.add('hidden');
    mainContainer.classList.remove('hidden');

    // Reset views
    codeView.classList.remove('hidden');
    graphView.classList.add('hidden');
    searchView.classList.add('hidden');
    switchTab('code');

    statusSpan.textContent = `Context: ${repo.name} (${repo.branch})`;

    loadFiles(repo.id);
}

// --- TABS & NAVIGATION ---




function switchTab(tabName) {
    // Reset active state for Context Tabs
    [codeTabBtn, graphTabBtn, navigatorTabBtn].forEach(b => b.classList.remove('active'));
    [codeView, graphView, navigatorView].forEach(v => v.classList.add('hidden'));

    // Handle Search separately
    if (tabName === 'search') {
        searchTabBtn.classList.add('active');
        searchView.classList.remove('hidden');
        // Hide context views when searching? Or overlay?
        // Let's hide context views to be clean
        return;
    } else {
        searchTabBtn.classList.remove('active');
        searchView.classList.add('hidden');
    }

    // Activate selected context tab
    if (tabName === 'code') {
        codeTabBtn.classList.add('active');
        codeView.classList.remove('hidden');
    } else if (tabName === 'graph') {
        graphTabBtn.classList.add('active');
        graphView.classList.remove('hidden');
    } else if (tabName === 'navigator') {
        navigatorTabBtn.classList.add('active');
        navigatorView.classList.remove('hidden');
        if (selectedNodeId) loadNavigatorData(selectedNodeId);
    }
}

codeTabBtn.addEventListener('click', () => switchTab('code'));
graphTabBtn.addEventListener('click', () => switchTab('graph'));
navigatorTabBtn.addEventListener('click', () => switchTab('navigator'));
searchTabBtn.addEventListener('click', () => {
    if (searchTabBtn.classList.contains('active')) {
        // Toggle off
        switchTab('code'); // Default back to code
    } else {
        switchTab('search');
    }
});

// Global state for selected node
let selectedNodeId = null;

homeBtn.addEventListener('click', () => {
    mainContainer.classList.add('hidden');
    homeView.classList.remove('hidden');
    currentRepoId = null;
    statusSpan.textContent = "Ready (v2.1)";
    loadRepositories(); // Refresh list
});

closeSearchBtn.addEventListener('click', () => {
    switchTab('code'); // Go back to code view
});

// --- INDEXING ---

indexBtn.addEventListener('click', async () => {
    const path = repoPathInput.value.trim();
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

        // Reload repos to show the new one
        loadRepositories();
    } catch (e) {
        statusSpan.textContent = `Error: ${e.message}`;
        console.error(e);
    } finally {
        indexBtn.disabled = false;
    }
});

// --- FILE LOADING ---

async function loadFiles(repoId) {
    try {
        let url = `${API_BASE}/files`;
        if (repoId) url += `?repo_id=${repoId}`;

        const res = await fetch(url);
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

    // Reset UI state
    selectedNodeId = null;
    contextTabs.classList.add('hidden'); // Hide tabs until chunk selected
    switchTab('code');

    try {
        let url = `${API_BASE}/file_view?path=${encodeURIComponent(path)}`;
        if (currentRepoId) url += `&repo_id=${currentRepoId}`;

        const res = await fetch(url);
        if (!res.ok) throw new Error(await res.text());
        const data = await res.json();
        codeContainer.innerHTML = data.html;
    } catch (e) {
        codeContainer.textContent = `Error loading content: ${e.message}`;
    }
}

async function openGraph(chunkId) {
    selectedNodeId = chunkId; // Track selection

    // Show tabs now that we have a context
    contextTabs.classList.remove('hidden');

    // Optional: Auto-switch to graph? Or stay on code?
    // User said: "given a selected chunk I can decide whether to view..."
    // So maybe stay on code but show the options?
    // But usually clicking a chunk implies wanting to see details/graph.
    // Let's Default to Graph for now as it provides immediate visual feedback, 
    // OR stay on Code and let user click.
    // User complaint: "opens a section that goes down".
    // Let's try: Stay on Code, but show tabs.
    // actually, `openGraph` name implies opening graph.
    // Let's rename function logic or just switch tab.
    // Let's switch to 'graph' to show the "Apple style" transition.
    switchTab('graph');

    nodeDetails.innerHTML = '<div style="padding:15px; color:#858585;">Loading graph data...</div>';

    try {
        let url = `${API_BASE}/chunk/${chunkId}/graph`;
        if (currentRepoId) url += `?repo_id=${currentRepoId}`;

        const res = await fetch(url);
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

// --- NAVIGATOR LOGIC ---

async function loadNavigatorData(nodeId) {
    const sections = {
        neighbors: document.querySelector('#navNeighbors .nav-content'),
        parent: document.querySelector('#navParent .nav-content'),
        impact: document.querySelector('#navImpact .nav-content'),
        pipeline: document.getElementById('pipelineGraph')
    };

    // Clear previous
    Object.values(sections).forEach(el => el.innerHTML = 'Loading...');

    try {
        // 1. Neighbors
        const [prev, next] = await Promise.all([
            fetch(`${API_BASE}/navigator/${nodeId}/neighbor_prev`).then(r => r.json()),
            fetch(`${API_BASE}/navigator/${nodeId}/neighbor_next`).then(r => r.json())
        ]);

        sections.neighbors.innerHTML = `
            <div class="nav-item">
                <div class="label">Previous Chunk</div>
                <div class="val">${prev.id ? renderChunkCard(prev) : 'None'}</div>
            </div>
            <div class="nav-item">
                <div class="label">Next Chunk</div>
                <div class="val">${next.id ? renderChunkCard(next) : 'None'}</div>
            </div>
        `;

        // 2. Parent
        const parent = await fetch(`${API_BASE}/navigator/${nodeId}/parent`).then(r => r.json());
        sections.parent.innerHTML = parent.id ? `
            <div class="nav-item">
                <div class="label">Type</div>
                <div class="val">${parent.type}</div>
            </div>
            <div class="nav-item">
                <div class="label">File</div>
                <div class="val">${parent.file_path}</div>
            </div>
        ` : 'No parent context found.';

        // 3. Impact
        const impact = await fetch(`${API_BASE}/navigator/${nodeId}/impact`).then(r => r.json());
        if (impact.refs && impact.refs.length > 0) {
            sections.impact.innerHTML = impact.refs.map(ref => `
                <div class="nav-item">
                    <div class="label">${ref.type}</div>
                    <div class="val">${ref.file_path}:${ref.start_line}</div>
                </div>
            `).join('');
        } else {
            sections.impact.innerHTML = 'No incoming references found.';
        }

        // 4. Pipeline (Call Graph)
        const pipeline = await fetch(`${API_BASE}/navigator/${nodeId}/pipeline`).then(r => r.json());
        renderPipelineGraph(pipeline, sections.pipeline);

    } catch (e) {
        console.error("Navigator load failed", e);
        Object.values(sections).forEach(el => el.innerHTML = `Error: ${e.message}`);
    }
}

function renderChunkCard(node) {
    // Mini card with code snippet
    const content = node.content ? node.content.split('\n').slice(0, 5).join('\n') : '(No content)';
    return `
        <div style="margin-top:5px;">
            <div style="font-weight:bold; color:#ccc; font-size:11px;">${node.type} (L${node.start_line})</div>
            <pre style="background:#111; padding:5px; border-radius:3px; color:#858585; font-size:10px; overflow:hidden; margin:5px 0 0 0;">${escapeHtml(content)}</pre>
        </div>
    `;
}

function renderPipelineGraph(data, container) {
    container.innerHTML = ''; // Clear loading text

    // Convert tree to nodes/edges for vis-network
    const nodes = [];
    const edges = [];
    const visited = new Set();

    function traverse(nodeId, children, parentId = null, nodeData = null) {
        if (!nodeId || visited.has(nodeId)) return;
        visited.add(nodeId);

        // Construct node object compatible with createNodeSvg
        // If nodeData is provided (from children dict), use it.
        // For root node, we might not have full data here unless we fetch it or pass it.
        // But wait, data.root_node is just an ID.
        // We need content for root node too?
        // Actually, for the root node of the pipeline, it's the CURRENT node.
        // We can assume we have it or can fetch it?
        // Or we can just render it simply if missing.

        const displayNode = {
            id: nodeId,
            file_path: nodeData ? nodeData.file : 'Current',
            start_line: nodeData ? nodeData.start_line : '?',
            end_line: '?',
            content: nodeData ? nodeData.content : '(Current Node)'
        };

        nodes.push({
            id: nodeId,
            image: createNodeSvg(displayNode, nodeId === data.root_node),
            shape: 'image',
            shapeProperties: { useImageSize: true }
        });

        if (parentId) {
            edges.push({
                from: parentId,
                to: nodeId,
                arrows: 'to',
                color: { color: '#585858', opacity: 0.8 },
                width: 1.5,
                smooth: { type: 'cubicBezier', forceDirection: 'horizontal', roundness: 0.4 }
            });
        }

        if (children) {
            Object.entries(children).forEach(([childId, childData]) => {
                traverse(childId, childData.children, nodeId, childData);
            });
        }
    }

    // We don't have root node data in the 'pipeline' response structure for the root itself,
    // only for children.
    // But we know the root is the selected node.
    // We can pass a placeholder or try to use the selected node data if available globally?
    // Let's use a placeholder for now or try to pass it.
    traverse(data.root_node, data.call_graph);

    if (nodes.length === 0) {
        container.innerHTML = 'No outgoing calls found.';
        return;
    }

    const netData = { nodes: new vis.DataSet(nodes), edges: new vis.DataSet(edges) };
    const options = {
        layout: {
            hierarchical: false // Use physics layout like main graph
        },
        physics: {
            enabled: true,
            solver: 'barnesHut',
            barnesHut: {
                gravitationalConstant: -200000,
                centralGravity: 0.1,
                springLength: 400,
                springConstant: 0.01,
                damping: 0.3,
                avoidOverlap: 1
            },
            stabilization: {
                enabled: true,
                iterations: 1000,
                fit: true
            }
        },
        interaction: {
            hover: true,
            dragNodes: true,
            zoomView: true,
            dragView: true
        }
    };

    const network = new vis.Network(container, netData, options);

    network.once("stabilizationIterationsDone", function () {
        network.setOptions({ physics: { enabled: false } });
        network.fit({ animation: { duration: 500, easingFunction: 'easeInOutQuad' } });
    });
}

// --- SEARCH ---

searchBtn.addEventListener('click', performSearch);
searchInput.addEventListener('keypress', (e) => {
    if (e.key === 'Enter') performSearch();
});

async function performSearch() {
    const query = searchInput.value.trim();
    if (!query) return;

    if (!currentRepoId) {
        alert("Please select a repository first.");
        return;
    }

    searchResults.innerHTML = '<div style="color:#888; text-align:center; margin-top:20px;">Searching...</div>';

    try {
        const res = await fetch(`${API_BASE}/search`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                query: query,
                repo_id: currentRepoId,
                strategy: searchStrategy.value
            })
        });

        if (!res.ok) throw new Error(await res.text());
        const data = await res.json();
        renderSearchResults(data.results);
    } catch (e) {
        searchResults.innerHTML = `<div style="color:red; padding:20px;">Search failed: ${e.message}</div>`;
    }
}

function renderSearchResults(results) {
    searchResults.innerHTML = '';
    if (results.length === 0) {
        searchResults.innerHTML = '<div style="color:#888; text-align:center; margin-top:20px;">No results found.</div>';
        return;
    }

    results.forEach(res => {
        const div = document.createElement('div');
        div.className = 'search-result';

        const score = (res.score * 100).toFixed(1) + '%';

        div.innerHTML = `
            <div class="header">
                <span class="file-path">${escapeHtml(res.file_path)}:${res.start_line}</span>
                <div class="meta-badges">
                    <span class="badge score">${score}</span>
                    <span class="badge type">${res.chunk_type || 'CODE'}</span>
                </div>
            </div>
            <div class="snippet">${escapeHtml(res.content)}</div>
            <div style="font-size:11px; color:#666; margin-top:5px;">
                Method: ${res.retrieval_method}
            </div>
        `;

        div.addEventListener('click', () => {
            showSearchDetails(res);
        });

        searchResults.appendChild(div);
    });
}

function showSearchDetails(res) {
    // Create modal if not exists
    let modal = document.getElementById('searchDetailModal');
    if (!modal) {
        modal = document.createElement('div');
        modal.id = 'searchDetailModal';
        modal.className = 'modal-overlay';
        document.body.appendChild(modal);
    }

    const close = () => modal.remove();

    modal.innerHTML = `
        <div class="modal-content">
            <div class="modal-header">
                <h3 style="margin:0; color:#fff;">Search Result Details</h3>
                <button style="background:none; border:none; color:#ccc; font-size:20px; cursor:pointer;" id="closeModalBtn">×</button>
            </div>
            <div class="modal-body">
                <div class="detail-section">
                    <h4>Metadata</h4>
                    <div class="detail-row"><span class="detail-label">Node ID:</span><span class="detail-value">${res.node_id}</span></div>
                    <div class="detail-row"><span class="detail-label">Repo ID:</span><span class="detail-value">${res.repo_id}</span></div>
                    <div class="detail-row"><span class="detail-label">Branch:</span><span class="detail-value">${res.branch}</span></div>
                    <div class="detail-row"><span class="detail-label">File:</span><span class="detail-value">${res.file_path}</span></div>
                    <div class="detail-row"><span class="detail-label">Lines:</span><span class="detail-value">${res.start_line} - ${res.end_line}</span></div>
                    <div class="detail-row"><span class="detail-label">Type:</span><span class="detail-value">${res.chunk_type}</span></div>
                    <div class="detail-row"><span class="detail-label">Score:</span><span class="detail-value">${res.score}</span></div>
                    <div class="detail-row"><span class="detail-label">Method:</span><span class="detail-value">${res.retrieval_method}</span></div>
                </div>

                <div class="detail-section">
                    <h4>Content</h4>
                    <pre style="background:#1e1e1e; padding:10px; border-radius:4px; color:#d4d4d4; font-family:'Consolas', monospace; overflow-x:auto;">${escapeHtml(res.content)}</pre>
                </div>

                <div class="detail-section">
                    <h4>Context Analysis</h4>
                    <div class="detail-row"><span class="detail-label">Parent:</span><span class="detail-value">${res.parent_context || 'None'}</span></div>
                    <div class="detail-row" style="align-items:flex-start;">
                        <span class="detail-label">Outgoing Refs:</span>
                        <div class="detail-value">
                            ${(res.outgoing_definitions && res.outgoing_definitions.length) ?
            res.outgoing_definitions.map(d => `<span style="display:inline-block; background:#333; padding:2px 5px; margin:2px; border-radius:3px;">${escapeHtml(d)}</span>`).join('')
            : 'None'}
                        </div>
                    </div>
                </div>
                
                <div class="detail-section">
                    <h4>Raw Data</h4>
                    <pre style="background:#111; padding:10px; border-radius:4px; color:#888; font-size:10px; overflow-x:auto;">${escapeHtml(JSON.stringify(res, null, 2))}</pre>
                </div>
                
                <div style="margin-top:20px; text-align:right;">
                    <button id="jumpToCodeBtn" style="background:#0e639c; color:white; border:none; padding:8px 16px; border-radius:4px; cursor:pointer;">Jump to Code</button>
                </div>
            </div>
        </div>
    `;

    modal.querySelector('#closeModalBtn').addEventListener('click', close);
    modal.addEventListener('click', (e) => {
        if (e.target === modal) close();
    });

    modal.querySelector('#jumpToCodeBtn').addEventListener('click', () => {
        close();
        loadFileView(res.file_path);
    });
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
            📄 ${escapeXml(node.file_path ? node.file_path.split('/').pop() : 'Unknown File')}
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
    const container = document.getElementById('graphContainer');
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
        const otherPath = (c.other && c.other.file_path) ? c.other.file_path.split('/').pop() : 'Unknown';
        const otherName = c.other ? (otherPath + ':' + c.other.start_line) : 'Unknown';
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
