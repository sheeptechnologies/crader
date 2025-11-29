import pytest
import os
import subprocess
from code_graph_indexer import CodebaseIndexer, CodeRetriever
from code_graph_indexer.storage.sqlite import SqliteGraphStorage

@pytest.mark.slow
def test_full_flow(tmp_path, mock_embedder):
    # Setup paths
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    db_path = tmp_path / "index.db"
    
    # Create a dummy repo
    (repo_path / "main.py").write_text("def hello():\n    print('world')")
    
    # Initialize git repo
    subprocess.run(["git", "init"], cwd=repo_path, check=True)
    subprocess.run(["git", "config", "user.email", "you@example.com"], cwd=repo_path, check=True)
    subprocess.run(["git", "config", "user.name", "Your Name"], cwd=repo_path, check=True)
    subprocess.run(["git", "add", "."], cwd=repo_path, check=True)
    subprocess.run(["git", "commit", "-m", "Initial commit"], cwd=repo_path, check=True)

    # Initialize Storage
    storage = SqliteGraphStorage(str(db_path))

    # Patch TreeSitterRepoParser to return dummy chunks
    from unittest.mock import patch, MagicMock
    from code_graph_indexer.models import FileRecord, ChunkNode, ChunkContent
    
    with patch('code_graph_indexer.indexer.TreeSitterRepoParser') as MockParser:
        # Setup mock parser instance
        mock_parser_instance = MockParser.return_value
        
        # Mock metadata provider
        mock_parser_instance.metadata_provider.get_repo_info.return_value = {
            'repo_id': 'test_repo',
            'commit_hash': 'hash',
            'branch': 'main',
            'name': 'test_repo',
            'url': 'http://test'
        }
        
        # Mock stream_semantic_chunks
        def stream_chunks(*args, **kwargs):
            file_rec = FileRecord(
                id="f1", repo_id="test_repo", commit_hash="hash", file_hash="fh",
                path="main.py", language="python", size_bytes=100, category="code",
                indexed_at="2023-01-01T00:00:00Z"
            )
            node = ChunkNode(
                id="n1", file_id="f1", file_path="main.py", chunk_hash="ch1",
                type="function", start_line=1, end_line=2, byte_range=[0, 20],
                metadata={}
            )
            content = ChunkContent(chunk_hash="ch1", content="def hello():\n    print('world')")
            yield (file_rec, [node], [content], [])
            
        mock_parser_instance.stream_semantic_chunks.side_effect = stream_chunks

        # Initialize Indexer (it will use the mocked parser class)
        indexer = CodebaseIndexer(str(repo_path), storage)
        
        # 1. Indexing
        indexer.index()
    
    # Verify indexing happened
    stats = storage.get_stats()
    assert stats['total_nodes'] > 0
    
    # 2. Embedding (using mock embedder to save time/network)
    # We need to consume the generator
    # Note: indexer.embed uses self.parser.metadata_provider, so we need to ensure indexer.parser is our mock
    # Since we patched the class, indexer.parser should be our mock_parser_instance
    list(indexer.embed(mock_embedder))
    
    # 3. Retrieval
    retriever = CodeRetriever(storage, mock_embedder)
    # We search for "hello" which is in the code
    results = retriever.retrieve("hello", repo_id="test_repo")
    
    # Assertions
    assert len(results) > 0
    assert results[0].node_id is not None
