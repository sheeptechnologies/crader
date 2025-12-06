import React, { useState, useEffect } from 'react';
import axios from 'axios';
import { Link, useNavigate } from 'react-router-dom';
import { Search, Filter, FileText, Plus, X, Code } from 'lucide-react';

const API_BASE = 'http://localhost:8019/api';

const ROLES = [
    "entry_point", "api_endpoint", "test_case", "test_suite",
    "data_schema", "class", "function", "method", "module"
];

const CATEGORIES = [
    "test", "config", "docs", "code", "logic", "definition"
];

export default function SearchPage() {
    const navigate = useNavigate();
    const [query, setQuery] = useState('');
    const [results, setResults] = useState([]);
    const [repos, setRepos] = useState([]);
    const [selectedRepo, setSelectedRepo] = useState('');
    const [loading, setLoading] = useState(false);

    // Advanced Filters State
    const [filters, setFilters] = useState({
        language: '',
        role: [],
        exclude_role: [],
        category: [],
        exclude_category: []
    });

    useEffect(() => {
        axios.get(`${API_BASE}/repos`).then(res => setRepos(res.data));
    }, []);

    const toggleFilter = (type, value) => {
        setFilters(prev => {
            const current = prev[type];
            if (current.includes(value)) {
                return { ...prev, [type]: current.filter(item => item !== value) };
            } else {
                return { ...prev, [type]: [...current, value] };
            }
        });
    };

    const handleSearch = async (e) => {
        e.preventDefault();
        if (!selectedRepo) {
            alert('Please select a repository');
            return;
        }
        setLoading(true);

        // Build filters object
        const activeFilters = {};
        if (filters.language) activeFilters.language = filters.language;
        if (filters.role.length) activeFilters.role = filters.role;
        if (filters.exclude_role.length) activeFilters.exclude_role = filters.exclude_role;
        if (filters.category.length) activeFilters.category = filters.category;
        if (filters.exclude_category.length) activeFilters.exclude_category = filters.exclude_category;

        try {
            const res = await axios.post(`${API_BASE}/search`, {
                query,
                repo_id: selectedRepo,
                limit: 20,
                filters: Object.keys(activeFilters).length ? activeFilters : null
            });
            setResults(res.data);
        } catch (err) {
            console.error(err);
            alert('Search failed');
        } finally {
            setLoading(false);
        }
    };

    const FilterSection = ({ title, options, type, excludeType }) => (
        <div className="mb-4">
            <h4 className="text-xs font-bold text-gray-500 uppercase mb-2">{title}</h4>
            <div className="flex flex-wrap gap-2">
                {options.map(opt => {
                    const isInc = filters[type].includes(opt);
                    const isExc = filters[excludeType].includes(opt);
                    return (
                        <div key={opt} className="flex items-center bg-gray-900 rounded border border-gray-700 overflow-hidden">
                            <button
                                type="button"
                                onClick={() => {
                                    if (isExc) toggleFilter(excludeType, opt);
                                    toggleFilter(type, opt);
                                }}
                                className={`px-2 py-1 text-xs ${isInc ? 'bg-blue-600 text-white' : 'hover:bg-gray-800 text-gray-400'}`}
                            >
                                {opt}
                            </button>
                            <div className="w-[1px] h-full bg-gray-700"></div>
                            <button
                                type="button"
                                onClick={() => {
                                    if (isInc) toggleFilter(type, opt);
                                    toggleFilter(excludeType, opt);
                                }}
                                className={`px-2 py-1 text-xs ${isExc ? 'bg-red-600 text-white' : 'hover:bg-gray-800 text-gray-400'}`}
                                title="Exclude"
                            >
                                <X className="w-3 h-3" />
                            </button>
                        </div>
                    );
                })}
            </div>
        </div>
    );

    return (
        <div className="p-8 max-w-7xl mx-auto h-full flex flex-col">
            <h1 className="text-3xl font-bold mb-8">Search Codebase</h1>

            <div className="bg-gray-800 p-6 rounded-xl border border-gray-700 mb-8">
                <form onSubmit={handleSearch} className="space-y-6">
                    <div className="flex gap-4">
                        <div className="w-1/4">
                            <select
                                value={selectedRepo}
                                onChange={e => setSelectedRepo(e.target.value)}
                                className="w-full p-3 bg-gray-900 border border-gray-600 rounded-lg focus:outline-none focus:border-blue-500"
                                required
                            >
                                <option value="">Select Repository...</option>
                                {repos.map(r => (
                                    <option key={r.id} value={r.id}>{r.name} ({r.branch})</option>
                                ))}
                            </select>
                        </div>
                        <div className="flex-1 relative">
                            <Search className="absolute left-3 top-3.5 text-gray-400 w-5 h-5" />
                            <input
                                type="text"
                                value={query}
                                onChange={e => setQuery(e.target.value)}
                                placeholder="Search query (e.g. 'auth logic', 'User class')..."
                                className="w-full pl-10 p-3 bg-gray-900 border border-gray-600 rounded-lg focus:outline-none focus:border-blue-500"
                            />
                        </div>
                        <button
                            type="submit"
                            disabled={loading}
                            className="px-8 py-3 bg-blue-600 hover:bg-blue-700 rounded-lg font-medium disabled:opacity-50"
                        >
                            {loading ? 'Searching...' : 'Search'}
                        </button>
                    </div>

                    <div className="border-t border-gray-700 pt-4">
                        <div className="flex gap-8">
                            <div className="flex-1">
                                <FilterSection
                                    title="Semantic Roles"
                                    options={ROLES}
                                    type="role"
                                    excludeType="exclude_role"
                                />
                            </div>
                            <div className="flex-1">
                                <FilterSection
                                    title="Categories"
                                    options={CATEGORIES}
                                    type="category"
                                    excludeType="exclude_category"
                                />
                            </div>
                            <div className="w-48">
                                <h4 className="text-xs font-bold text-gray-500 uppercase mb-2">Language</h4>
                                <input
                                    placeholder="e.g. python"
                                    value={filters.language}
                                    onChange={e => setFilters({ ...filters, language: e.target.value })}
                                    className="w-full bg-gray-900 border border-gray-600 rounded px-3 py-1.5 text-sm"
                                />
                            </div>
                        </div>
                    </div>
                </form>
            </div>

            {/* Results */}
            <div className="flex-1 overflow-auto space-y-6">
                {results.map((result, i) => (
                    <div
                        key={i}
                        className="bg-gray-800 border border-gray-700 rounded-xl overflow-hidden hover:border-blue-500/50 transition-all cursor-pointer group"
                        onClick={() => navigate(`/chunk/${result.node_id}`)}
                    >
                        <div className="bg-gray-900/50 p-3 border-b border-gray-700 flex justify-between items-center">
                            <div className="flex items-center gap-3">
                                <FileText className="w-4 h-4 text-blue-400" />
                                <span className="font-mono text-sm text-blue-300">
                                    {result.file_path}
                                    <span className="text-gray-500">:{result.start_line}-{result.end_line}</span>
                                </span>
                                <div className="flex gap-2">
                                    {result.semantic_labels?.map(label => (
                                        <span key={label} className="text-[10px] uppercase font-bold bg-blue-900/30 text-blue-300 px-2 py-0.5 rounded">
                                            {label}
                                        </span>
                                    ))}
                                </div>
                            </div>
                            <div className="flex items-center gap-4">
                                <span className="text-xs text-gray-500">Score: {result.score.toFixed(3)}</span>
                                <Code className="w-4 h-4 text-gray-600 group-hover:text-blue-400 transition-colors" />
                            </div>
                        </div>

                        <div className="p-0 bg-[#1e1e1e]">
                            <pre className="p-4 text-sm font-mono text-gray-300 overflow-x-auto whitespace-pre-wrap">
                                {result.content}
                            </pre>
                        </div>
                    </div>
                ))}

                {results.length === 0 && !loading && (
                    <div className="text-center text-gray-500 mt-12 flex flex-col items-center gap-4">
                        <Search className="w-12 h-12 opacity-20" />
                        <p>No results found. Try adjusting your query or filters.</p>
                    </div>
                )}
            </div>
        </div>
    );
}
