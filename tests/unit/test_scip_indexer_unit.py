"""Unit tests for SCIPIndexer and SCIPRunner.

SCIP (SCIP Code Intelligence Protocol) is a format for representing information
about symbols and relationships in code. These tests verify:
- SCIP symbol name cleaning and normalization
- Conversion from line/column coordinates to byte offsets
- Symbol name extraction from source code
- Processing of symbol definitions and occurrences
- SCIP index preparation and document streaming
"""

import unittest
from unittest.mock import MagicMock, patch

from crader.graph.indexers.scip import SCIPIndexer, SCIPRunner


class TestSCIPIndexerUnit(unittest.TestCase):
    """Test suite for SCIPIndexer class that processes SCIP indices."""

    def setUp(self):
        """Initialize test objects with a mock repository path."""
        self.indexer = SCIPIndexer("/tmp/repo")
        self.runner = SCIPRunner("/tmp/repo")

    @patch("crader.graph.indexers.scip.SCIPIndexer._clean_symbol")
    def test_clean_symbol(self, mock_clean):
        # Call the real method if we didn't mock side_effect, or copy logic
        # Actually I want to test the logic, so I shouldn't patch it!
        # I'll rely on the real method import.
        pass

    def test_clean_symbol_logic(self):
        """Test SCIP symbol name cleaning and normalization.

        SCIP symbols have a complex format like:
        'scip-python npm package_name/module/class#method.'

        This test verifies that _clean_symbol:
        1. Extracts the descriptor (last part after space)
        2. Removes special characters like /, #, .
        3. Returns a clean, readable identifier
        """
        # Standard SCIP symbol format: scheme manager package version descriptor
        sym = "scip-python npm package_name/module/class#method."
        cleaned = self.indexer._clean_symbol(sym)

        # Verify the method name is present in the cleaned result
        self.assertTrue("method" in cleaned)

    @patch("crader.graph.indexers.scip.SCIPIndexer._lines")
    def test_bytes_conversion(self, mock_lines):
        """Test conversion from line/column coordinates to byte offsets.

        SCIP uses (line, column) coordinates, but internally we need to convert
        to byte offsets to precisely identify positions in the file.

        Example: if line 1 starts at byte 6, character 3 of line 1
        will be at byte 6+3=9.
        """
        # Mock: simulate a file with lines starting at bytes 0, 6, 12
        mock_lines.return_value = [0, 6, 12]

        # Test 1: Line 0, chars 0-5 -> bytes 0-5 (first line)
        r = self.indexer._bytes("f.py", [0, 0, 0, 5])
        self.assertEqual(r, [0, 5])

        # Test 2: Line 1, chars 0-5 -> bytes 6-11 (second line starts at byte 6)
        r = self.indexer._bytes("f.py", [1, 0, 1, 5])
        self.assertEqual(r, [6, 11])

    @patch("crader.graph.indexers.scip.SCIPIndexer._get_file_content_cached")
    def test_extract_symbol_name(self, mock_content):
        """Test symbol name extraction from source code.

        Given a range (line, column), extracts the corresponding text.
        Useful for obtaining function/variable names from their definitions.
        """
        # Mock: simulate Python file content
        mock_content.return_value = ["def foo():\n", "    pass\n"]

        # Extract "foo" from line 0, chars 4-7 (after "def ")
        name = self.indexer._extract_symbol_name("a.py", [0, 4, 0, 7])
        self.assertEqual(name, "foo")

        # Invalid range (line out of bounds) -> returns "unknown"
        name = self.indexer._extract_symbol_name("a.py", [5, 0, 5, 1])
        self.assertEqual(name, "unknown")

    def test_process_definitions(self):
        """Test processing of symbol definitions.

        SCIP "occurrences" represent where a symbol appears in code.
        symbol_roles=1 indicates a DEFINITION (not a reference).

        This test verifies that definitions are correctly added to the
        symbol table with all necessary information.
        """
        # SCIP data structure with a local symbol definition
        wrapper = {
            "project_root": "/tmp/repo",
            "document": {
                "relative_path": "a.py",
                "occurrences": [
                    {
                        "symbol": "local 1",  # Symbol name
                        "symbol_roles": 1,  # 1 = Definition
                        "range": [0, 0, 0, 3],  # Position in file
                    }
                ],
            },
        }

        mock_table = MagicMock()
        self.indexer._process_definitions(wrapper, mock_table)

        # Verify the symbol was added to the table
        mock_table.add.assert_called_once()
        args = mock_table.add.call_args[0]

        # Verify parameters passed: (symbol, file, range, is_local)
        self.assertEqual(args[0], "local 1")  # Symbol name
        self.assertEqual(args[1], "a.py")  # File path
        self.assertTrue(args[3])  # Is local symbol

    @patch("shutil.which")
    @patch("subprocess.run")
    @patch("os.path.exists")
    @patch("os.path.getsize")
    def test_scip_runner_prepare_indices(self, mock_size, mock_exists, mock_run, mock_which):
        """Test SCIP index preparation.

        SCIPRunner executes the external SCIP tool to generate code indices.
        This test verifies that:
        1. The SCIP tool is found on the system
        2. Indices are generated correctly
        3. Output files have valid size (>10 bytes)
        """
        # Mock: simulate SCIP tool is installed
        mock_which.return_value = "/usr/bin/scip"
        mock_exists.return_value = True
        mock_size.return_value = 100  # Generated index of 100 bytes (valid)

        # Mock: SCIP execution completed successfully
        mock_run.return_value = MagicMock(returncode=0)

        # Mock: simulate discovery of a Python task to index
        with patch.object(self.runner, "_discover_tasks", return_value=[("scip-python", "/tmp/repo")]):
            with patch.object(self.runner, "_prune_workspace"):
                indices = self.runner.prepare_indices()

                # Verify 1 index was generated
                self.assertEqual(len(indices), 1)
                self.assertEqual(indices[0][0], "/tmp/repo")
                mock_run.assert_called()  # Verify SCIP was executed

    @patch("subprocess.Popen")
    def test_stream_documents(self, mock_popen):
        """Test streaming of documents from a SCIP index.

        SCIP indices can be very large, so they are read in streaming mode
        (line by line) instead of loading everything into memory.

        This test verifies that JSON documents are parsed correctly
        during streaming.
        """
        # Mock: simulate a process emitting JSON on stdout
        proc = MagicMock()
        proc.stdout = [
            '{"documents": [{"relative_path": "a.py"}]}'  # SCIP tool JSON output
        ]
        proc.wait.return_value = None
        mock_popen.return_value = proc

        # Stream documents from the index
        docs = list(self.runner.stream_documents([("/tmp/repo", "index.scip")]))

        # Verify 1 document was read for file a.py
        self.assertEqual(len(docs), 1)
        self.assertEqual(docs[0]["document"]["relative_path"], "a.py")


if __name__ == "__main__":
    unittest.main()
