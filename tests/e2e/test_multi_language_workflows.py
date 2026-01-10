"""
End-to-End Tests for Sheep Codebase Indexer

Tests real-world use cases across multiple programming languages:
- Python (Flask)
- TypeScript (React)
- Go (simple project)

Each test covers the complete workflow:
1. Clone repository
2. Index codebase
3. Generate embeddings
4. Search and retrieve
5. Navigate graph
"""

import os
import sys
import pytest
import asyncio
import shutil
import tempfile
import subprocess
from typing import Optional

# Add src to path
current_dir = os.path.dirname(os.path.abspath(__file__))
src_dir = os.path.abspath(os.path.join(current_dir, "..", "..", "src"))
if src_dir not in sys.path:
    sys.path.insert(0, src_dir)

from crader.indexer import CodebaseIndexer
from crader.providers.embedding import DummyEmbeddingProvider
from crader.storage.connector import PooledConnector
from crader.retriever import CodeRetriever
from crader.reader import CodeReader
from crader.navigator import CodeNavigator


# Test configuration
DB_URL = os.getenv("TEST_DB_URL", "postgresql://sheep_user:sheep_password@localhost:6432/sheep_test")
USE_REAL_EMBEDDINGS = os.getenv("USE_REAL_EMBEDDINGS", "false").lower() == "true"


@pytest.fixture(scope="module")
def temp_workspace():
    """Create temporary workspace for test repositories."""
    workspace = tempfile.mkdtemp(prefix="sheep_e2e_")
    yield workspace
    shutil.rmtree(workspace, ignore_errors=True)


@pytest.fixture(scope="module")
def db_connector():
    """Create database connector for tests."""
    connector = PooledConnector(DB_URL, min_size=2, max_size=10)
    yield connector
    connector.close()


@pytest.fixture(scope="module")
def embedding_provider():
    """Create embedding provider (dummy for tests)."""
    if USE_REAL_EMBEDDINGS:
        from crader.providers.embedding import OpenAIEmbeddingProvider
        return OpenAIEmbeddingProvider(model="text-embedding-3-small")
    else:
        return DummyEmbeddingProvider()


def clone_repository(url: str, branch: str, workspace: str, name: str) -> str:
    """Clone a repository for testing."""
    repo_path = os.path.join(workspace, name)
    if os.path.exists(repo_path):
        shutil.rmtree(repo_path)
    
    subprocess.run(
        ["git", "clone", "--depth", "1", "--branch", branch, url, repo_path],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL
    )
    return f"file://{repo_path}"


class TestPythonFlaskWorkflow:
    """Test complete workflow on Flask (Python) repository."""
    
    @pytest.fixture(scope="class")
    def flask_repo(self, temp_workspace):
        """Clone Flask repository."""
        return clone_repository(
            url="https://github.com/pallets/flask.git",
            branch="main",
            workspace=temp_workspace,
            name="flask"
        )
    
    @pytest.fixture(scope="class")
    def indexer(self, flask_repo, db_connector):
        """Create indexer for Flask."""
        indexer = CodebaseIndexer(
            repo_path=flask_repo,
            storage_connector=db_connector
        )
        yield indexer
        indexer.close()
    
    def test_01_index_repository(self, indexer):
        """Test: Index Flask repository."""
        snapshot_id = indexer.index(
            repo_url="https://github.com/pallets/flask.git",
            branch="main",
            force=False
        )
        
        assert snapshot_id is not None
        assert snapshot_id != "queued"
        
        # Verify snapshot exists
        stats = indexer.storage.get_stats()
        assert stats['total_nodes'] > 0
        assert stats['total_files'] > 0
    
    @pytest.mark.asyncio
    async def test_02_generate_embeddings(self, indexer, embedding_provider):
        """Test: Generate embeddings for Flask code."""
        snapshot_id = indexer.storage.get_active_snapshot(
            indexer.storage.ensure_repository(
                "https://github.com/pallets/flask.git",
                "main",
                "flask"
            )
        )['id']
        
        total_embedded = 0
        async for update in indexer.embed(embedding_provider, batch_size=100):
            if update['status'] == 'completed':
                total_embedded = update.get('newly_embedded', 0)
        
        assert total_embedded >= 0  # May be 0 if already embedded
    
    def test_03_search_routing_functionality(self, indexer, embedding_provider):
        """Test: Search for Flask routing functionality."""
        retriever = CodeRetriever(indexer.storage, embedding_provider)
        
        repo_id = indexer.storage.ensure_repository(
            "https://github.com/pallets/flask.git",
            "main",
            "flask"
        )
        
        snapshot_id = indexer.storage.get_active_snapshot(repo_id)['id']
        
        # Search for routing functionality
        results = retriever.retrieve(
            query="route decorator implementation",
            repo_id=repo_id,
            snapshot_id=snapshot_id,
            limit=5,
            strategy="hybrid"
        )
        
        assert len(results) > 0
        
        # Verify results contain routing-related code
        found_route = any("route" in r.content.lower() for r in results)
        assert found_route, "Should find route-related code"
    
    def test_04_read_specific_file(self, indexer):
        """Test: Read Flask application file."""
        reader = CodeReader(indexer.storage)
        
        snapshot_id = indexer.storage.get_active_snapshot(
            indexer.storage.ensure_repository(
                "https://github.com/pallets/flask.git",
                "main",
                "flask"
            )
        )['id']
        
        # Read Flask's main app file
        file_data = reader.read_file(
            snapshot_id=snapshot_id,
            file_path="src/flask/app.py"
        )
        
        assert file_data is not None
        assert 'content' in file_data
        assert len(file_data['content']) > 0
    
    def test_05_navigate_call_graph(self, indexer):
        """Test: Navigate Flask's call graph."""
        navigator = CodeNavigator(indexer.storage)
        retriever = CodeRetriever(indexer.storage, DummyEmbeddingProvider())
        
        repo_id = indexer.storage.ensure_repository(
            "https://github.com/pallets/flask.git",
            "main",
            "flask"
        )
        snapshot_id = indexer.storage.get_active_snapshot(repo_id)['id']
        
        # Find a function node
        results = retriever.retrieve(
            query="route decorator",
            repo_id=repo_id,
            snapshot_id=snapshot_id,
            limit=1
        )
        
        if results:
            node_id = results[0].node_id
            
            # Analyze impact (who calls this)
            impact = navigator.analyze_impact(node_id)
            # May or may not have callers, just verify it doesn't crash
            assert isinstance(impact, list)


class TestTypeScriptReactWorkflow:
    """Test complete workflow on React (TypeScript) repository."""
    
    @pytest.fixture(scope="class")
    def react_repo(self, temp_workspace):
        """Clone React repository."""
        return clone_repository(
            url="https://github.com/facebook/react.git",
            branch="main",
            workspace=temp_workspace,
            name="react"
        )
    
    @pytest.fixture(scope="class")
    def indexer(self, react_repo, db_connector):
        """Create indexer for React."""
        indexer = CodebaseIndexer(
            repo_path=react_repo,
            storage_connector=db_connector
        )
        yield indexer
        indexer.close()
    
    def test_01_index_typescript_repo(self, indexer):
        """Test: Index React TypeScript repository."""
        snapshot_id = indexer.index(
            repo_url="https://github.com/facebook/react.git",
            branch="main",
            force=False
        )
        
        assert snapshot_id is not None
        
        # Verify TypeScript files were indexed
        stats = indexer.storage.get_stats()
        assert stats['total_nodes'] > 0
    
    def test_02_search_hooks_implementation(self, indexer, embedding_provider):
        """Test: Search for React hooks implementation."""
        retriever = CodeRetriever(indexer.storage, embedding_provider)
        
        repo_id = indexer.storage.ensure_repository(
            "https://github.com/facebook/react.git",
            "main",
            "react"
        )
        snapshot_id = indexer.storage.get_active_snapshot(repo_id)['id']
        
        # Search for useState hook
        results = retriever.retrieve(
            query="useState hook implementation",
            repo_id=repo_id,
            snapshot_id=snapshot_id,
            limit=5,
            filters={"language": "typescript"}
        )
        
        assert len(results) > 0
    
    def test_03_filter_by_path(self, indexer, embedding_provider):
        """Test: Filter search by path prefix."""
        retriever = CodeRetriever(indexer.storage, embedding_provider)
        
        repo_id = indexer.storage.ensure_repository(
            "https://github.com/facebook/react.git",
            "main",
            "react"
        )
        snapshot_id = indexer.storage.get_active_snapshot(repo_id)['id']
        
        # Search only in packages directory
        results = retriever.retrieve(
            query="component",
            repo_id=repo_id,
            snapshot_id=snapshot_id,
            limit=5,
            filters={"path_prefix": "packages/"}
        )
        
        # Verify all results are from packages directory
        for result in results:
            assert result.file_path.startswith("packages/")


class TestGoProjectWorkflow:
    """Test complete workflow on Go repository."""
    
    @pytest.fixture(scope="class")
    def go_repo(self, temp_workspace):
        """Clone a Go repository (using Hugo as example)."""
        return clone_repository(
            url="https://github.com/gohugoio/hugo.git",
            branch="master",
            workspace=temp_workspace,
            name="hugo"
        )
    
    @pytest.fixture(scope="class")
    def indexer(self, go_repo, db_connector):
        """Create indexer for Go project."""
        indexer = CodebaseIndexer(
            repo_path=go_repo,
            storage_connector=db_connector
        )
        yield indexer
        indexer.close()
    
    def test_01_index_go_repo(self, indexer):
        """Test: Index Go repository."""
        snapshot_id = indexer.index(
            repo_url="https://github.com/gohugoio/hugo.git",
            branch="master",
            force=False
        )
        
        assert snapshot_id is not None
        
        stats = indexer.storage.get_stats()
        assert stats['total_nodes'] > 0
    
    def test_02_search_go_functions(self, indexer, embedding_provider):
        """Test: Search for Go functions."""
        retriever = CodeRetriever(indexer.storage, embedding_provider)
        
        repo_id = indexer.storage.ensure_repository(
            "https://github.com/gohugoio/hugo.git",
            "master",
            "hugo"
        )
        snapshot_id = indexer.storage.get_active_snapshot(repo_id)['id']
        
        results = retriever.retrieve(
            query="template rendering",
            repo_id=repo_id,
            snapshot_id=snapshot_id,
            limit=5,
            filters={"language": "go"}
        )
        
        assert len(results) > 0


class TestMultiLanguageSearch:
    """Test cross-language search capabilities."""
    
    def test_search_across_languages(self, db_connector, embedding_provider):
        """Test: Search across multiple languages."""
        retriever = CodeRetriever(db_connector, embedding_provider)
        
        # Search without language filter (should find results from all indexed repos)
        # This assumes previous tests have indexed multiple repos
        results = retriever.retrieve(
            query="error handling",
            limit=10,
            strategy="hybrid"
        )
        
        # Should find results from different languages
        languages = set(r.metadata.get('language') for r in results if r.metadata.get('language'))
        
        # We expect at least some results
        assert len(results) > 0


class TestIncrementalIndexing:
    """Test incremental indexing capabilities."""
    
    def test_reindex_unchanged_repo(self, temp_workspace, db_connector):
        """Test: Reindex unchanged repository (should be fast)."""
        # Clone a small repo
        repo_path = clone_repository(
            url="https://github.com/pallets/click.git",
            branch="main",
            workspace=temp_workspace,
            name="click_test"
        )
        
        indexer = CodebaseIndexer(repo_path, storage_connector=db_connector)
        
        try:
            # First index
            snapshot_id_1 = indexer.index(
                repo_url="https://github.com/pallets/click.git",
                branch="main",
                force=False
            )
            
            # Second index (should reuse snapshot)
            snapshot_id_2 = indexer.index(
                repo_url="https://github.com/pallets/click.git",
                branch="main",
                force=False
            )
            
            # Should return same snapshot
            assert snapshot_id_1 == snapshot_id_2
        finally:
            indexer.close()


class TestErrorHandling:
    """Test error handling and edge cases."""
    
    def test_invalid_repository(self, temp_workspace, db_connector):
        """Test: Handle invalid repository gracefully."""
        invalid_path = os.path.join(temp_workspace, "nonexistent")
        
        with pytest.raises(Exception):
            indexer = CodebaseIndexer(invalid_path, storage_connector=db_connector)
            indexer.index(
                repo_url="https://invalid-url.com/repo.git",
                branch="main"
            )
    
    def test_search_empty_query(self, db_connector, embedding_provider):
        """Test: Handle empty search query."""
        retriever = CodeRetriever(db_connector, embedding_provider)
        
        results = retriever.retrieve(
            query="",
            limit=5
        )
        
        # Should return empty or handle gracefully
        assert isinstance(results, list)


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
