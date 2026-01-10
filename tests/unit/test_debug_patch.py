
import unittest
from unittest.mock import patch
import crader.indexer

class TestDebugPatch(unittest.TestCase):
    def test_attr_existence(self):
        print(f"Indexer module: {crader.indexer}")
        print(f"Attrs: {dir(crader.indexer)}")
        self.assertTrue(hasattr(crader.indexer, 'PostgresGraphStorage'), "Attribute missing")

    @patch("crader.indexer.PostgresGraphStorage")
    def test_patching(self, mock_pg):
        self.assertTrue(True)
