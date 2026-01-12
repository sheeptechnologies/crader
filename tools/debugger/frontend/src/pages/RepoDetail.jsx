import React, { useEffect, useState } from 'react';
import { useParams, Link } from 'react-router-dom';
import axios from 'axios';
import { PanelLeft, X, MessageSquare, Search } from 'lucide-react';
import FileTree from '../components/FileTree';
import CodeViewer from '../components/CodeViewer';
import GraphViewer from '../components/GraphViewer';

const API_BASE = 'http://localhost:8019/api';

export default function RepoDetail() {
    const { repoId } = useParams();
    const [files, setFiles] = useState([]);
    const [selectedFile, setSelectedFile] = useState(null);
    const [fileContent, setFileContent] = useState('');
    const [chunks, setChunks] = useState([]);
    const [selectedChunk, setSelectedChunk] = useState(null);
    const [graphData, setGraphData] = useState(null);
    const [showGraph, setShowGraph] = useState(false);
    const [error, setError] = useState(null);

    useEffect(() => {
        fetchFiles();
    }, [repoId]);

    const fetchFiles = async () => {
        try {
            const res = await axios.get(`${API_BASE}/repos/${repoId}/files`);
            setFiles(res.data);
        } catch (err) {
            console.error(err);
        }
    };

    const handleFileSelect = async (path) => {
        setSelectedFile(path);
        setError(null);
        setFileContent('');
        setChunks([]);
        setShowGraph(false);
        setGraphData(null);
        try {
            const res = await axios.get(`${API_BASE}/repos/${repoId}/file_content`, {
                params: { path }
            });
            setFileContent(res.data.content);
            setChunks(res.data.chunks);
        } catch (err) {
            console.error(err);
        }
    };

    const handleChunkClick = async (chunk) => {
        setSelectedChunk(chunk);
        setShowGraph(true);
        try {
            const res = await axios.get(`${API_BASE}/chunks/${chunk.id}/graph`);
            setGraphData(res.data);
        } catch (err) {
            console.error(err);
        }
    };

    return (
        <div className="flex h-full">
            {/* File Tree Sidebar */}
            <div className="w-64 bg-gray-900 border-r border-gray-800 flex flex-col">
                <div className="p-4 border-b border-gray-800 font-semibold text-gray-300 flex items-center gap-2">
                    <PanelLeft className="w-4 h-4" /> Files
                </div>
                <div className="flex-1 overflow-auto">
                    <FileTree files={files} onSelectFile={handleFileSelect} />
                </div>
            </div>

            {/* Main Content */}
            <div className="flex-1 flex flex-col relative">
                {/* Top Bar */}
                <div className="h-14 border-b border-gray-800 bg-gray-900 flex items-center justify-between px-4 flex-shrink-0">
                    <div className="font-medium text-gray-300 truncate">
                        {selectedFile ? selectedFile.split('/').pop() : 'Repository Browser'}
                    </div>
                    <div className="flex items-center gap-2">
                        <Link to="/search" className="p-2 hover:bg-gray-800 rounded-lg text-gray-400 hover:text-white transition-colors" title="Advanced Search">
                            <Search className="w-5 h-5" />
                        </Link>
                        <Link to={`/chat/${repoId}`} className="flex items-center gap-2 px-3 py-1.5 bg-blue-600 hover:bg-blue-500 rounded-lg text-sm font-medium text-white transition-colors shadow-lg shadow-blue-900/20">
                            <MessageSquare className="w-4 h-4" />
                            <span>Chat</span>
                        </Link>
                    </div>
                </div>
                {error ? (
                    <div className="flex-1 flex items-center justify-center text-red-500 p-8 text-center">
                        <div>
                            <h3 className="text-xl font-bold mb-2">Error Loading File</h3>
                            <p>{error}</p>
                        </div>
                    </div>
                ) : selectedFile ? (
                    <CodeViewer
                        content={fileContent}
                        chunks={chunks}
                        onChunkClick={handleChunkClick}
                    />
                ) : (
                    <div className="flex-1 flex items-center justify-center text-gray-500">
                        Select a file to view content
                    </div>
                )}

                {/* Graph Overlay/Modal */}
                {showGraph && (
                    <div className="absolute inset-0 bg-gray-900/95 z-50 flex flex-col">
                        <div className="p-4 border-b border-gray-800 flex justify-between items-center bg-gray-900">
                            <h3 className="font-bold text-lg">Dependency Graph</h3>
                            <button onClick={() => setShowGraph(false)} className="p-2 hover:bg-gray-800 rounded text-gray-400 hover:text-white">
                                <X className="w-6 h-6" />
                            </button>
                        </div>
                        <div className="flex-1 flex">
                            <div className="flex-1">
                                <GraphViewer data={graphData} onNodeClick={(id) => console.log(id)} />
                            </div>
                            {/* Side Panel for Chunk Details */}
                            <div className="w-80 border-l border-gray-800 p-4 overflow-auto bg-gray-900">
                                <h4 className="font-bold mb-4 text-blue-400">Node Details</h4>
                                {selectedChunk && (
                                    <div className="space-y-4">
                                        <div>
                                            <label className="text-xs text-gray-500 uppercase">ID</label>
                                            <div className="text-sm font-mono break-all">{selectedChunk.id}</div>
                                        </div>
                                        <div>
                                            <label className="text-xs text-gray-500 uppercase">Type</label>
                                            <div className="text-sm">{selectedChunk.metadata?.type || 'Unknown'}</div>
                                        </div>
                                        <div>
                                            <label className="text-xs text-gray-500 uppercase">Lines</label>
                                            <div className="text-sm">{selectedChunk.start_line} - {selectedChunk.end_line}</div>
                                        </div>
                                        <div>
                                            <label className="text-xs text-gray-500 uppercase">Metadata</label>
                                            <pre className="text-xs bg-gray-800 p-2 rounded mt-1 overflow-auto">
                                                {JSON.stringify(selectedChunk.metadata, null, 2)}
                                            </pre>
                                        </div>
                                    </div>
                                )}
                            </div>
                        </div>
                    </div>
                )}
            </div>
        </div>
    );
}
