import sys
from unittest.mock import MagicMock

# Mock tree-sitter dependencies if not available
try:
    import tree_sitter
    import tree_sitter_languages
except ImportError:
    sys.modules["tree_sitter"] = MagicMock()
    sys.modules["tree_sitter.Parser"] = MagicMock()
    sys.modules["tree_sitter.Node"] = MagicMock()
    sys.modules["tree_sitter_languages"] = MagicMock()

import pytest
from code_graph_indexer.storage.sqlite import SqliteGraphStorage
from code_graph_indexer.providers.embedding import EmbeddingProvider

@pytest.fixture
def mock_storage():
    # Use in-memory DB for extreme speed
    return SqliteGraphStorage(db_path=":memory:")

@pytest.fixture
def mock_embedder():
    provider = MagicMock(spec=EmbeddingProvider)
    # Always return a zero vector of dim 384
    provider.embed.return_value = [[0.1] * 384]
    provider.dimension = 384
    provider.model_name = "mock-model"
    return provider
