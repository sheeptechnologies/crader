import React, { useEffect, useState } from 'react';
import axios from 'axios';
import { Link } from 'react-router-dom';
import { Plus, GitBranch, Database, HardDrive, Trash2 } from 'lucide-react';

const API_BASE = 'http://localhost:8019/api';

export default function RepoList() {
    const [repos, setRepos] = useState([]);
    const [newRepoUrl, setNewRepoUrl] = useState('');
    const [loading, setLoading] = useState(false);

    useEffect(() => {
        fetchRepos();
    }, []);

    const fetchRepos = async () => {
        try {
            const res = await axios.get(`${API_BASE}/repos`);
            setRepos(res.data);
        } catch (err) {
            console.error(err);
        }
    };

    const handleAddRepo = async (e) => {
        e.preventDefault();
        setLoading(true);
        try {
            await axios.post(`${API_BASE}/repos`, { path_or_url: newRepoUrl });
            setNewRepoUrl('');
            fetchRepos();
        } catch (err) {
            alert('Failed to add repo');
        } finally {
            setLoading(false);
        }
    };

    const handleIndex = async (e, repoId) => {
        e.preventDefault();
        try {
            await axios.post(`${API_BASE}/repos/${repoId}/index`, { force: true });
            alert('Indexing started in background');
            fetchRepos(); // Update status if we polled, but here just refresh
        } catch (err) {
            alert('Failed to start indexing');
        }
    };

    const handleEmbed = async (e, repoId) => {
        e.preventDefault();
        try {
            await axios.post(`${API_BASE}/repos/${repoId}/embed`, { provider: 'openai' });
            alert('Embedding started in background');
        } catch (err) {
            const msg = err.response?.data?.detail || 'Failed to start embedding';
            alert(msg);
        }
    };

    const handleDelete = async (e, repoId) => {
        e.preventDefault(); // Prevent navigation
        if (!confirm("Are you sure you want to delete this repo?")) return;

        try {
            await axios.delete(`${API_BASE}/repos/${repoId}`);
            fetchRepos();
        } catch (error) {
            console.error("Delete failed", error);
            alert("Failed to delete repo");
        }
    };

    return (
        <div className="p-8 max-w-6xl mx-auto">
            <h1 className="text-3xl font-bold mb-8">Repositories</h1>

            {/* Add Repo Form */}
            <form onSubmit={handleAddRepo} className="mb-8 flex gap-4">
                <input
                    type="text"
                    value={newRepoUrl}
                    onChange={(e) => setNewRepoUrl(e.target.value)}
                    placeholder="Git URL or Local Path"
                    className="flex-1 p-3 bg-gray-800 border border-gray-700 rounded-lg focus:outline-none focus:border-blue-500"
                />
                <button
                    type="submit"
                    disabled={loading}
                    className="px-6 py-3 bg-blue-600 hover:bg-blue-700 rounded-lg font-medium flex items-center gap-2 disabled:opacity-50"
                >
                    <Plus className="w-5 h-5" />
                    Add Repository
                </button>
            </form>

            {/* Repo Grid */}
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
                {repos.map((repo) => (
                    <div key={repo.id} className="bg-gray-800 border border-gray-700 rounded-xl p-6 hover:border-gray-600 transition-colors">
                        <div className="flex justify-between items-start mb-4">
                            <div>
                                <h3 className="text-xl font-semibold mb-1">{repo.name}</h3>
                                <p className="text-sm text-gray-400 flex items-center gap-1">
                                    <GitBranch className="w-3 h-3" /> {repo.branch}
                                </p>
                            </div>
                            <span className={`px-2 py-1 text-xs rounded-full ${repo.status === 'indexed' ? 'bg-green-900 text-green-300' :
                                repo.status === 'indexing' ? 'bg-yellow-900 text-yellow-300' :
                                    'bg-gray-700 text-gray-300'
                                }`}>
                                {repo.status || 'unknown'}
                            </span>
                        </div>

                        <div className="flex gap-2 mt-4">
                            <Link
                                to={`/repo/${repo.id}`}
                                className="flex-1 px-3 py-2 bg-gray-700 hover:bg-gray-600 rounded text-center text-sm"
                            >
                                Explore
                            </Link>
                            <button
                                onClick={(e) => handleIndex(e, repo.id)}
                                className="px-3 py-2 bg-gray-700 hover:bg-gray-600 rounded text-sm"
                                title="Index"
                            >
                                <Database className="w-4 h-4" />
                            </button>
                            <button
                                onClick={(e) => handleEmbed(e, repo.id)}
                                className="px-3 py-2 bg-gray-700 hover:bg-gray-600 rounded text-sm"
                                title="Embed"
                            >
                                <HardDrive className="w-4 h-4" />
                            </button>
                            <button
                                onClick={(e) => handleDelete(e, repo.id)}
                                className="px-3 py-2 bg-red-700 hover:bg-red-600 rounded text-sm"
                                title="Delete"
                            >
                                <Trash2 className="w-4 h-4" />
                            </button>
                        </div>
                    </div>
                ))}
            </div>
        </div>
    );
}
