import React, { useEffect, useState } from 'react';
import { useParams, Link, useNavigate } from 'react-router-dom';
import axios from 'axios';
import ReactFlow, { Background, Controls, Handle, Position, useNodesState, useEdgesState } from 'reactflow';
import 'reactflow/dist/style.css';
import dagre from 'dagre';
import { ArrowLeft, GitCommit, ArrowUp, ArrowDown, Activity, Layers, Code } from 'lucide-react';

const API_BASE = 'http://localhost:8019/api';

// Rich Node Component with Code Snippet
const RichNode = ({ data }) => (
    <div className={`shadow-xl rounded-lg border-2 w-[500px] overflow-hidden bg-[#1e1e1e] ${data.isCenter ? 'border-blue-500 ring-2 ring-blue-500/30' : 'border-gray-700 hover:border-gray-500'}`}>
        <Handle type="target" position={Position.Top} className="!bg-gray-500 !w-3 !h-3" />

        {/* Header */}
        <div className="bg-gray-800 px-3 py-2 border-b border-gray-700 flex justify-between items-center">
            <div className="flex items-center gap-2 overflow-hidden">
                <Code className="w-3 h-3 text-blue-400 flex-shrink-0" />
                <span className="text-xs font-bold text-gray-200 truncate" title={data.filePath}>
                    {data.fileName}
                </span>
            </div>
            <span className="text-[10px] bg-gray-700 text-gray-300 px-1.5 py-0.5 rounded font-mono">
                {data.lines}
            </span>
        </div>

        {/* Semantic Label */}
        {data.semanticLabel && (
            <div className="px-3 py-1 bg-blue-900/20 border-b border-gray-800">
                <span className="text-[10px] uppercase font-bold text-blue-300 tracking-wider">
                    {data.semanticLabel}
                </span>
            </div>
        )}

        {/* Code Snippet */}
        <div className="p-3 bg-[#1e1e1e]">
            <pre className="text-[10px] font-mono text-gray-400 whitespace-pre overflow-x-auto leading-relaxed">
                {data.codeSnippet}
            </pre>
        </div>

        <Handle type="source" position={Position.Bottom} className="!bg-gray-500 !w-3 !h-3" />
    </div>
);

const nodeTypes = { rich: RichNode };

const VIEW_DESCRIPTIONS = {
    neighbors: "Showing linear neighbors in the file. The chunk immediately before and after this one.",
    parent: "Showing the structural parent. For a method, this is usually the Class it belongs to.",
    impact: "Showing incoming references. These are the chunks that call or use the current chunk.",
    pipeline: "Showing the flow of data. This view attempts to visualize how data moves through the system involving this chunk."
};

export default function ChunkNavigator() {
    const { chunkId } = useParams();
    const navigate = useNavigate();
    const [chunk, setChunk] = useState(null);
    const [graphData, setGraphData] = useState(null);
    const [loading, setLoading] = useState(true);
    const [activeTab, setActiveTab] = useState('neighbors');

    // React Flow State
    const [nodes, setNodes, onNodesChange] = useNodesState([]);
    const [edges, setEdges, onEdgesChange] = useEdgesState([]);

    useEffect(() => {
        fetchChunkData();
    }, [chunkId]);

    // Re-layout when tab or data changes
    useEffect(() => {
        fetchChunkData();
    }, [chunkId, activeTab]);

    useEffect(() => {
        if (graphData && chunk) {
            updateGraphLayout();
        }
    }, [graphData, chunk, activeTab]);

    const fetchChunkData = async () => {
        setLoading(true);
        try {
            let url = `${API_BASE}/chunks/${chunkId}/graph`;
            if (activeTab === 'impact') {
                url = `${API_BASE}/chunks/${chunkId}/impact`;
            } else if (activeTab === 'pipeline') {
                url = `${API_BASE}/chunks/${chunkId}/pipeline`;
            } else if (activeTab === 'neighbors') {
                url = `${API_BASE}/chunks/${chunkId}/neighbors`;
            } else if (activeTab === 'parent') {
                url = `${API_BASE}/chunks/${chunkId}/parent`;
            }

            const res = await axios.get(url);
            setGraphData(res.data);

            // For specialized views, the center node might be in the list but we want to ensure we have the main chunk details
            const center = res.data.nodes.find(n => n.id === chunkId);
            if (center) setChunk(center);
            else if (!chunk) {
                // If center not found in response and we don't have it yet, try to fetch it directly from neighbors endpoint which usually includes it
                // or just set error state if it's truly missing
                console.warn("Center chunk not found in response");
            }

        } catch (err) {
            console.error(err);
        } finally {
            setLoading(false);
        }
    };

    const updateGraphLayout = () => {
        // All tabs now use specialized endpoints that return exactly what we need
        let relevantNodes = graphData?.nodes || [];
        let relevantEdges = graphData?.edges || [];

        const layouted = layoutGraph(relevantNodes, relevantEdges);
        setNodes(layouted.nodes);
        setEdges(layouted.edges);
    };

    const getFilteredView = () => {
        // This function is now redundant as logic moved to updateGraphLayout
        return { nodes: [], edges: [] };
    };

    const layoutGraph = (nodes, edges) => {
        const g = new dagre.graphlib.Graph();
        g.setGraph({ rankdir: 'TB', ranksep: 100, nodesep: 80 }); // Top-to-Bottom layout for better code reading
        g.setDefaultEdgeLabel(() => ({}));

        // Set larger dimensions for RichNode based on content
        nodes.forEach(node => {
            const lineCount = (node.content || "").split('\n').length;
            const height = Math.max(150, 60 + (lineCount * 16));
            g.setNode(node.id, { width: 500, height: height });
        });

        edges.forEach(edge => {
            g.setEdge(edge.source, edge.target);
        });

        dagre.layout(g);

        const flowNodes = nodes.map(node => {
            const nodeWithPos = g.node(node.id);
            const isCenter = node.id === chunkId;

            // Extract label and snippet
            let semanticLabel = node.metadata?.type || "Chunk";
            if (node.metadata?.semantic_matches?.[0]?.value) {
                semanticLabel = node.metadata.semantic_matches[0].value;
            }

            // Use full content instead of truncated snippet
            const codeContent = node.content || "";
            // Estimate height based on line count (rough approx: 20px per line + header/padding)
            const lineCount = codeContent.split('\n').length;
            const estimatedHeight = Math.max(150, 60 + (lineCount * 16));

            return {
                id: node.id,
                type: 'rich', // Use our new RichNode
                position: { x: nodeWithPos.x - 250, y: nodeWithPos.y - (estimatedHeight / 2) }, // Center based on dynamic height
                data: {
                    fileName: node.file_path.split('/').pop(),
                    filePath: node.file_path,
                    lines: `${node.start_line}-${node.end_line}`,
                    semanticLabel,
                    codeSnippet: codeContent, // Pass full content
                    isCenter
                },
                style: { width: 500, height: estimatedHeight } // Pass dimensions to ReactFlow node style if needed, though RichNode handles inner sizing
            };
        });

        const flowEdges = edges.map((edge, i) => ({
            id: `${edge.source}-${edge.target}-${edge.relation}-${i}`,
            source: edge.source,
            target: edge.target,
            label: edge.relation,
            type: 'smoothstep',
            animated: true,
            style: { stroke: '#4b5563', strokeWidth: 2 },
            labelStyle: { fill: '#9ca3af', fontSize: 10 }
        }));

        return { nodes: flowNodes, edges: flowEdges };
    };

    if (loading) return <div className="h-full flex items-center justify-center text-gray-500">Loading chunk data...</div>;
    if (!chunk) return <div className="h-full flex items-center justify-center text-red-500">Chunk not found</div>;

    return (
        <div className="h-full flex flex-col bg-gray-900 text-white">
            {/* Header */}
            <div className="p-4 border-b border-gray-800 flex justify-between items-center bg-gray-900 shadow-sm z-10">
                <div className="flex items-center gap-4">
                    <Link to="/search" className="p-2 hover:bg-gray-800 rounded-full transition-colors">
                        <ArrowLeft className="w-5 h-5 text-gray-400" />
                    </Link>
                    <div>
                        <h1 className="text-lg font-bold flex items-center gap-2">
                            <GitCommit className="w-5 h-5 text-blue-500" />
                            Chunk Navigator
                        </h1>
                        <p className="text-xs text-gray-400 font-mono mt-0.5">
                            {chunk.file_path}:{chunk.start_line}-{chunk.end_line}
                        </p>
                    </div>
                </div>

                <div className="flex bg-gray-800 rounded-lg p-1">
                    <TabButton
                        active={activeTab === 'neighbors'}
                        onClick={() => setActiveTab('neighbors')}
                        icon={<ArrowUp className="w-4 h-4" />}
                        label="Neighbors"
                    />
                    <TabButton
                        active={activeTab === 'parent'}
                        onClick={() => setActiveTab('parent')}
                        icon={<Layers className="w-4 h-4" />}
                        label="Parent"
                    />
                    <TabButton
                        active={activeTab === 'impact'}
                        onClick={() => setActiveTab('impact')}
                        icon={<ArrowDown className="w-4 h-4" />}
                        label="Impact"
                    />
                    <TabButton
                        active={activeTab === 'pipeline'}
                        onClick={() => setActiveTab('pipeline')}
                        icon={<Activity className="w-4 h-4" />}
                        label="Pipeline"
                    />
                </div>
            </div>

            <div className="flex-1 flex overflow-hidden">
                {/* Graph Panel */}
                <div className="w-full flex flex-col bg-gray-900 relative">
                    <div className="absolute top-4 left-4 z-10 bg-gray-800/90 backdrop-blur border border-gray-700 p-3 rounded-lg max-w-md shadow-lg">
                        <h3 className="text-sm font-bold text-blue-400 mb-1 capitalize">{activeTab} View</h3>
                        <p className="text-xs text-gray-300 leading-relaxed">
                            {VIEW_DESCRIPTIONS[activeTab]}
                        </p>
                    </div>

                    <div className="flex-1">
                        <ReactFlow
                            nodes={nodes}
                            edges={edges}
                            onNodesChange={onNodesChange}
                            onEdgesChange={onEdgesChange}
                            nodeTypes={nodeTypes}
                            onNodeClick={(_, node) => {
                                if (node.id !== chunkId) {
                                    navigate(`/chunk/${node.id}`);
                                }
                            }}
                            fitView
                            minZoom={0.1}
                            attributionPosition="bottom-right"
                        >
                            <Background color="#374151" gap={20} size={1} />
                            <Controls className="!bg-gray-800 !border-gray-700 !fill-gray-400" />
                        </ReactFlow>
                    </div>
                </div>
            </div>
        </div>
    );
}

const TabButton = ({ active, onClick, icon, label }) => (
    <button
        onClick={onClick}
        className={`flex items-center gap-2 px-3 py-1.5 rounded-md text-xs font-medium transition-all ${active
            ? 'bg-blue-600 text-white shadow-lg'
            : 'text-gray-400 hover:text-white hover:bg-gray-700'
            }`}
    >
        {icon}
        {label}
    </button>
);
