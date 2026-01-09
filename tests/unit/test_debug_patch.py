
import unittest
from unittest.mock import patch
import code_graph_indexer.indexer

class TestDebugPatch(unittest.TestCase):
    def test_attr_existence(self):
        print(f"Indexer module: {code_graph_indexer.indexer}")
        print(f"Attrs: {dir(code_graph_indexer.indexer)}")
        self.assertTrue(hasattr(code_graph_indexer.indexer, 'PostgresGraphStorage'), "Attribute missing")

    @patch("code_graph_indexer.indexer.PostgresGraphStorage")
    def test_patching(self, mock_pg):
        self.assertTrue(True)
