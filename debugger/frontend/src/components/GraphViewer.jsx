import React, { useEffect, useCallback } from 'react';
import ReactFlow, {
    Background,
    Controls,
    useNodesState,
    useEdgesState,
    MarkerType
} from 'reactflow';
import 'reactflow/dist/style.css';
import dagre from 'dagre';

const nodeWidth = 250;
const nodeHeight = 100;

const getLayoutedElements = (nodes, edges, direction = 'LR') => {
    const dagreGraph = new dagre.graphlib.Graph();
    dagreGraph.setDefaultEdgeLabel(() => ({}));

    dagreGraph.setGraph({ rankdir: direction, ranksep: 100, nodesep: 50 });

    nodes.forEach((node) => {
        dagreGraph.setNode(node.id, { width: nodeWidth, height: nodeHeight });
    });

    edges.forEach((edge) => {
        dagreGraph.setEdge(edge.source, edge.target);
    });

    dagre.layout(dagreGraph);

    const layoutedNodes = nodes.map((node) => {
        const nodeWithPosition = dagreGraph.node(node.id);
        node.targetPosition = 'left';
        node.sourcePosition = 'right';

        // We are shifting the dagre node position (anchor=center center) to the top left
        // so it matches the React Flow node anchor point (top left).
        node.position = {
            x: nodeWithPosition.x - nodeWidth / 2,
            y: nodeWithPosition.y - nodeHeight / 2,
        };

        return node;
    });

    return { nodes: layoutedNodes, edges };
};

export default function GraphViewer({ data, onNodeClick }) {
    const [nodes, setNodes, onNodesChange] = useNodesState([]);
    const [edges, setEdges, onEdgesChange] = useEdgesState([]);

    useEffect(() => {
        if (!data) return;

        const initialNodes = data.nodes.map(n => ({
            id: n.id,
            data: {
                label: (
                    <div className="p-2 border border-gray-700 rounded bg-gray-900 text-xs text-left">
                        <div className="font-bold text-blue-400 mb-1">{n.file_path.split('/').pop()}</div>
                        <div className="text-gray-400 mb-1 text-[10px]">Lines: {n.start_line}-{n.end_line}</div>
                        <div className="bg-black p-1 rounded overflow-auto h-32">
                            <pre className="text-[10px] text-gray-300 font-mono whitespace-pre">{n.content}</pre>
                        </div>
                    </div>
                )
            },
            position: { x: 0, y: 0 },
            style: {
                width: 300, // Wider nodes
                border: n.type === 'center' ? '2px solid #3b82f6' : '1px solid #555',
                borderRadius: '8px',
                background: '#111',
                color: 'white'
            }
        }));

        const initialEdges = data.edges.map((e, i) => ({
            id: `e${i}`,
            source: e.source,
            target: e.target,
            label: e.relation,
            type: 'smoothstep',
            markerEnd: { type: MarkerType.ArrowClosed },
            animated: true,
            style: { stroke: '#555' }
        }));

        const { nodes: layoutedNodes, edges: layoutedEdges } = getLayoutedElements(
            initialNodes,
            initialEdges
        );

        setNodes(layoutedNodes);
        setEdges(layoutedEdges);
    }, [data, setNodes, setEdges]);

    return (
        <div className="w-full h-full bg-gray-950">
            <ReactFlow
                nodes={nodes}
                edges={edges}
                onNodesChange={onNodesChange}
                onEdgesChange={onEdgesChange}
                onNodeClick={(_, node) => onNodeClick(node.id)}
                fitView
            >
                <Background color="#333" gap={16} />
                <Controls />
            </ReactFlow>
        </div>
    );
}
