"""Unit tests for TreeSitterRepoParser.

TreeSitter is a library for parsing source code that creates Abstract Syntax Trees (AST).
These tests verify:
- Code chunking (splitting into manageable pieces)
- Semantic metadata extraction (functions, classes, etc.)
- Handling of large nodes exceeding MAX_CHUNK_SIZE
- File filtering logic
"""

import unittest
from unittest.mock import MagicMock, patch, PropertyMock
from code_graph_indexer.parsing.parser import TreeSitterRepoParser
from code_graph_indexer.models import ChunkNode

class TestTreeSitterRepoParser(unittest.TestCase):
    """Test suite for TreeSitterRepoParser class that processes source code."""
    
    def setUp(self):
        """Initialize parser with mocks to avoid filesystem dependencies."""
        with patch('os.path.isdir', return_value=True):
            with patch('os.path.exists', return_value=True):
                 with patch("code_graph_indexer.parsing.parser.GitMetadataProvider") as mock_meta:
                     self.parser = TreeSitterRepoParser("/tmp/repo")

    def test_chunking_with_overlap(self):
        """Test chunk creation with overlap and semantic metadata.
        
        Chunking splits code into smaller pieces to:
        1. Limit chunk size (MAX_CHUNK_SIZE)
        2. Maintain context through overlap between adjacent chunks
        3. Preserve semantic metadata (type: function, class, etc.)
        
        This is fundamental for embedding and semantic search.
        """
        source_code = b"def foo():\n    pass\n"
        
        # Mock AST root node
        mock_root = MagicMock()
        mock_root.start_byte = 0
        mock_root.end_byte = len(source_code)
        
        # Mock function node in AST
        mock_func_node = MagicMock()
        mock_func_node.start_byte = 0
        mock_func_node.end_byte = len(source_code)
        mock_func_node.children = []
        mock_func_node.type = "function_definition"
        
        mock_root.children = [mock_func_node]
        mock_root.child_by_field_name.return_value = None  # Prevents TypeError 
        
        # Semantic captures: information extracted from AST
        # Contains metadata like function name, type, position
        captures = [{
            "node": mock_func_node, 
            "name": "function",
            "start": 0,
            "end": len(source_code),
            "metadata": {"identifier": "foo", "type": "function"}
        }]
        
        with patch.object(TreeSitterRepoParser, '_extract_tags', return_value=[]):
            # Lists to collect parsing results
            nodes = []      # Created code chunks
            contents = {}   # Textual content of chunks
            relations = []  # Relationships between chunks (e.g., calls)
            
            # Process code and create chunks
            self.parser._process_scope(
                mock_root, memoryview(source_code), source_code, "test.py", "f1", None,
                nodes, contents, relations, semantic_captures=captures
            )
            
            # Find chunks representing functions
            func_chunks = []
            for n in nodes:
                 matches = n.metadata.get('semantic_matches', [])
                 if any(m.get('type') == 'function' for m in matches):
                     func_chunks.append(n)
            
            # Verify 1 chunk was created for the function
            self.assertEqual(len(func_chunks), 1)
            
            # Verify metadata contains the identifier "foo"
            matches = func_chunks[0].metadata['semantic_matches']
            self.assertEqual(matches[0]['identifier'], "foo")

    def test_recursive_chunking_small_file(self):
        """Test simple chunking for a small file without exceeding MAX_CHUNK_SIZE."""
        # Setup similar to above...
        source_code = b"print('hello')"
        mock_root = MagicMock()
        mock_root.start_byte = 0
        mock_root.end_byte = len(source_code)
        mock_root.child_by_field_name.return_value = None # Fix TypeError iterator_node
        
        # Mock child
        mock_child = MagicMock()
        mock_child.start_byte = 0
        mock_child.end_byte = len(source_code)
        mock_child.type = "expression_statement"
        mock_child.children = [] # No children
        
        mock_root.children = [mock_child]
        
        nodes = []
        contents = {}
        relations = []
        
        self.parser._process_scope(
            parent_node=mock_root, 
            content_mv=memoryview(source_code), 
            full_content_bytes=source_code,
            file_path="test.py", 
            file_id="f1", 
            parent_chunk_id=None,
            nodes=nodes, 
            contents=contents, 
            relations=relations
        )
        
        self.assertTrue(len(nodes) > 0)
        # We expect at least one chunk for the content
        # Actually _extract_tags logic is simpler, expression_statement likely has no tags unless blocked
        # But here checking existence

    def test_should_process_file(self):
        """Test file filtering logic."""
        self.parser.all_ignore_dirs = {".git", "node_modules"}
        self.parser.EXT_TO_LANG_CONFIG = {".py": "python"}
        
        with patch("code_graph_indexer.parsing.parser.LANGUAGE_SPECIFIC_FILTERS", { "python": {"exclude_extensions": {".pyc"}} }):
            self.assertTrue(self.parser._should_process_file("src/main.py"))
            self.assertFalse(self.parser._should_process_file("node_modules/pkg/index.js"))
            self.assertFalse(self.parser._should_process_file(".git/config"))

    def test_handle_large_node_breakdown(self):
        """Test that large nodes are broken down recursively."""
        self.parser.MAX_CHUNK_SIZE = 10 
        
        source_code = b"x = 1\n" * 10
        
        class DummyNode:
            def __init__(self, start, end, children=None, type="program"):
                self.start_byte = start
                self.end_byte = end
                self.children = children or []
                self.type = type
                self.parent = None
            
            def child_by_field_name(self, name):
                return None
                
        child1 = DummyNode(0, 20, type="class_definition")
        mock_root = DummyNode(0, len(source_code), children=[child1])
        
        nodes = []
        
        with patch.object(self.parser, '_handle_large_node', wraps=self.parser._handle_large_node) as mock_breakdown:
             self.parser._process_scope(
                mock_root, memoryview(source_code), source_code, "file.py", "f1", None,
                nodes, {}, []
            )
             mock_breakdown.assert_called()

    def test_extract_tags(self):
        """Test tag extraction logic."""
        # Async
        node_async = MagicMock()
        node_async.type = "async_function_definition"
        node_async.children = []
        tags = self.parser._extract_tags(node_async)
        self.assertIn("async", tags)
        
        # Decorated
        node_dec = MagicMock()
        node_dec.type = "decorated_definition"
        node_dec.children = []
        tags = self.parser._extract_tags(node_dec)
        self.assertIn("decorated", tags)
        
        # Exported
        node_exp = MagicMock()
        node_exp.type = "export_statement"
        node_exp.children = []
        node_exp.parent = None
        tags = self.parser._extract_tags(node_exp)
        self.assertIn("exported", tags)

if __name__ == '__main__':
    unittest.main()
