import React from 'react';
import { BrowserRouter as Router, Routes, Route, Link } from 'react-router-dom';
import { Home, FolderGit2, Search, Activity } from 'lucide-react';
import RepoList from './pages/RepoList';
import RepoDetail from "./pages/RepoDetail";
import SearchPage from "./pages/SearchPage";
import ChunkNavigator from "./pages/ChunkNavigator";
import AgentChat from "./pages/AgentChat";

function App() {
    return (
        <Router>
            <div className="flex h-screen bg-gray-900 text-white">
                {/* Sidebar */}
                <div className="w-16 bg-gray-950 flex flex-col items-center py-4 border-r border-gray-800">
                    <div className="mb-8">
                        <Activity className="w-8 h-8 text-blue-500" />
                    </div>
                    <nav className="flex flex-col gap-4">
                        <Link to="/" className="p-2 hover:bg-gray-800 rounded-lg transition-colors" title="Repositories">
                            <FolderGit2 className="w-6 h-6 text-gray-400 hover:text-white" />
                        </Link>
                        <Link to="/search" className="p-2 hover:bg-gray-800 rounded-lg transition-colors" title="Search">
                            <Search className="w-6 h-6 text-gray-400 hover:text-white" />
                        </Link>
                    </nav>
                </div>

                {/* Main Content */}
                <div className="flex-1 overflow-hidden">
                    <Routes>
                        <Route path="/" element={<RepoList />} />
                        <Route path="/repo/:repoId" element={<RepoDetail />} />
                        <Route path="/search" element={<SearchPage />} />
                        <Route path="/chunk/:chunkId" element={<ChunkNavigator />} />
                        <Route path="/chat/:repoId" element={<AgentChat />} />
                    </Routes>
                </div>
            </div>
        </Router>
    );
}

export default App;
