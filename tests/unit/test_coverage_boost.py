import os
import io
import json
import pytest
import sqlite3
import subprocess
from unittest.mock import MagicMock, patch, mock_open

# --- PARSER TESTS ---
from crader.parsing.parser import TreeSitterRepoParser

class TestParserCoverage:
    @patch("crader.parsing.parser.get_language")
    def test_init_language_load_failure(self, mock_get_lang):
        # Allow one lang to fail
        def side_effect(name):
            if name == "python":
                raise Exception("Boom")
            return MagicMock()
        mock_get_lang.side_effect = side_effect
        
        parser = TreeSitterRepoParser("/tmp")
        assert ".py" not in parser.languages
        assert ".js" in parser.languages  # Assuming defaults load
    
    def test_safe_read_file_binary(self):
        parser = TreeSitterRepoParser("/tmp")
        with patch("builtins.open", mock_open(read_data=b"\0binary")):
            with patch("os.path.getsize", return_value=100):
                content, error = parser._safe_read_file("foo.bin")
                assert content is None
                assert error == "Binary file detected"

    def test_safe_read_file_exception(self):
        parser = TreeSitterRepoParser("/tmp")
        with patch("builtins.open", side_effect=IOError("Disk fail")):
            with patch("os.path.getsize", return_value=100):
                content, error = parser._safe_read_file("foo.txt")
                assert content is None
                assert "Disk fail" in error

    def test_load_query_exception(self):
        parser = TreeSitterRepoParser("/tmp")
        with patch("builtins.open", side_effect=Exception("Read fail")):
            with patch("os.path.exists", return_value=True):
                q = parser._load_query_for_language("python")
                assert q is None

    def test_get_semantic_captures_compile_error(self):
        parser = TreeSitterRepoParser("/tmp")
        # Initialize languages
        mock_lang = MagicMock()
        mock_lang.query.side_effect = Exception("Compile error")
        parser.languages["python"] = mock_lang
        parser.LANGUAGE_MAP[".py"] = "python" # ensure mapping
        
        # Mock load query
        parser._load_query_for_language = MagicMock(return_value="(query)")
        
        parser._get_semantic_captures(MagicMock(), "python")
        assert parser._query_cache["python"] is None

    def test_get_semantic_captures_ignore_bad_captures(self):
        parser = TreeSitterRepoParser("/tmp")
        # Setup valid query mock
        mock_query = MagicMock()
        mock_node = MagicMock()
        # Returns (node, name) tuples. Name without dot should be ignored
        mock_query.captures.return_value = [
            (mock_node, "bad_name"),
            (mock_node, "role.class")
        ]
        parser._query_cache["python"] = mock_query
        
        mock_tree = MagicMock()
        res = parser._get_semantic_captures(mock_tree, "python")
        assert len(res) == 1
        assert res[0]["metadata"]["category"] == "role"


# --- SCIP TESTS ---
from crader.graph.indexers.scip import DiskSymbolTable, SCIPRunner, SCIPIndexer

class TestSCIPCoverage:
    def test_disk_symbol_table_close_error(self):
        table = DiskSymbolTable()
        # Mock os.remove to fail
        with patch("os.remove", side_effect=OSError("Remove fail")):
            table.close() # Should not raise
            
    def test_disk_symbol_table_get_fallback(self):
        table = DiskSymbolTable()
        # Add global definition (empty scope)
        table.cursor.execute("INSERT INTO defs VALUES (?, ?, ?, ?, ?, ?, ?)", 
                             ("MySym", "", "file.py", 1, 0, 1, 10))
        
        # Search with current_file scope (should miss and fallback to global)
        f, rng = table.get("MySym", "other.py")
        assert f == "file.py"
        assert rng == [1, 0, 1, 10]

    def test_scip_runner_prune_workspace_errors(self):
        runner = SCIPRunner("/tmp")
        with patch("os.walk") as mock_walk:
            mock_walk.return_value = [("/root", ["dir_to_rem"], ["file_to_rem.log"])]
            
            # 1. Directory removal failure
            with patch("shutil.rmtree", side_effect=OSError("Perm denied")):
                # 2. File removal failure
                with patch("os.remove", side_effect=OSError("File locked")):
                    runner._prune_workspace("/root")
            # Should complete without crashing

    @patch("shutil.which", return_value="/bin/scip")
    def test_scip_runner_run_single_index_failures(self, mock_which):
        runner = SCIPRunner("/tmp")
        
        # Case 1: Subprocess returns non-zero
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stderr="Subprocess error")
            res = runner._run_single_index(("scip-python", "/root"), {}, MagicMock())
            assert res is None

        # Case 2: Exception during run
        with patch("subprocess.run", side_effect=Exception("Spawn failed")):
            res = runner._run_single_index(("scip-python", "/root"), {}, MagicMock())
            assert res is None

    @patch("shutil.which", return_value="/bin/scip")
    def test_scip_runner_stream_documents_errors(self, mock_which):
        runner = SCIPRunner("/tmp")
        
        # Case: Malformed JSON output
        with patch("subprocess.Popen") as mock_popen:
            proc_mock = MagicMock()
            proc_mock.stdout = ["{valid_json: false}"] # Bad JSON
            proc_mock.wait.return_value = 0
            mock_popen.return_value = proc_mock
            
            gen = runner.stream_documents([("/root", "index.scip")])
            assert list(gen) == [] # Should handle ValueError silently

    def test_scip_clean_symbol(self):
        indexer = SCIPIndexer("/tmp")
        # Test clean symbol logic
        
        # 1. Simple local symbol
        assert indexer._clean_symbol("local 123") == "123"
        
        # 2. Symbol with no language extension (just replaces separators)
        assert indexer._clean_symbol("pypi/pkg/module#Class") == "pypi.pkg.module.Class"
        
        # 3. Symbol WITH language extension (should strip prefix)
        # "repo/src/file.py/MyClass" -> splits on ".py/" -> "MyClass"
        assert indexer._clean_symbol("repo/src/file.py/MyClass") == "MyClass"

    def test_ensure_repository(self):
        mock_connector = MagicMock()
        storage = PostgresGraphStorage(mock_connector)
        
        mock_conn = MagicMock()
        mock_connector.get_connection.return_value.__enter__.return_value = mock_conn
        mock_conn.execute.return_value.fetchone.return_value = {"id": "repo-uuid"}
        
        rid = storage.ensure_repository("http://url", "main", "MyRepo")
        assert rid == "repo-uuid"
        
    def test_prune_snapshot(self):
        mock_connector = MagicMock()
        storage = PostgresGraphStorage(mock_connector)
        mock_conn = MagicMock()
        mock_connector.get_connection.return_value.__enter__.return_value = mock_conn
        
        storage.prune_snapshot("snap-1")
        mock_conn.execute.assert_called_with("DELETE FROM files WHERE snapshot_id = %s", ("snap-1",))

    def test_get_active_snapshot_id(self):
        mock_connector = MagicMock()
        storage = PostgresGraphStorage(mock_connector)
        mock_conn = MagicMock()
        mock_connector.get_connection.return_value.__enter__.return_value = mock_conn
        
        # Case 1: Found
        mock_conn.execute.return_value.fetchone.return_value = {"current_snapshot_id": "snap-active"}
        assert storage.get_active_snapshot_id("repo-1") == "snap-active"
        
        # Case 2: Not found or null
        mock_conn.execute.return_value.fetchone.return_value = None
        assert storage.get_active_snapshot_id("repo-1") is None


# --- POSTGRES STORAGE TESTS ---
import psycopg
from crader.storage.postgres import PostgresGraphStorage

class TestPostgresCoverage:
    def test_create_snapshot_unique_violation(self):
        mock_connector = MagicMock()
        storage = PostgresGraphStorage(mock_connector)
        
        mock_conn = MagicMock()
        mock_connector.get_connection.return_value.__enter__.return_value = mock_conn
        
        # First call (check existing) returns None
        # Second call (insert) raises UniqueViolation
        # Third call (update dirty flag) succeeds
        mock_conn.execute.side_effect = [
            MagicMock(fetchone=lambda: None), # Check existing
            psycopg.errors.UniqueViolation("Duplicate"), # Insert
            MagicMock() # Update
        ]
        
        sid, is_new = storage.create_snapshot("repo-1", "sha-1")
        assert sid is None
        assert is_new is False

    def test_activate_snapshot(self):
        mock_connector = MagicMock()
        storage = PostgresGraphStorage(mock_connector)
        mock_conn = MagicMock()
        mock_connector.get_connection.return_value.__enter__.return_value = mock_conn
        
        storage.activate_snapshot("repo-1", "snap-1", {"nodes": 10}, {"a.py": {}})
        
        # Should execute 2 updates in transaction
        assert mock_conn.execute.call_count == 2
        assert mock_conn.transaction.called

    def test_fail_snapshot(self):
        mock_connector = MagicMock()
        storage = PostgresGraphStorage(mock_connector)
        mock_conn = MagicMock()
        mock_connector.get_connection.return_value.__enter__.return_value = mock_conn
        
        storage.fail_snapshot("snap-1", "Failed")
        mock_conn.execute.assert_called_once()
        assert "failed" in mock_conn.execute.call_args[0][0]

    def test_add_nodes_fast_error(self):
        mock_connector = MagicMock()
        storage = PostgresGraphStorage(mock_connector)
        
        mock_conn = MagicMock()
        mock_connector.get_connection.return_value.__enter__.return_value = mock_conn
        mock_cursor = mock_conn.cursor.return_value.__enter__.return_value
        
        # Simulate COPY error
        mock_cursor.copy.side_effect = Exception("Copy failed")
        
        nodes = [MagicMock(to_dict=lambda: {
            "id": "1", "file_path": "a.py", "start_line": 1, "end_line": 2, 
            "byte_range": [0, 10], "metadata": {}
        })]
        
        with pytest.raises(Exception, match="Copy failed"):
            storage.add_nodes_fast(nodes)

    def test_search_fts_exception(self):
        mock_connector = MagicMock()
        storage = PostgresGraphStorage(mock_connector)
        mock_conn = MagicMock()
        mock_connector.get_connection.return_value.__enter__.return_value = mock_conn
        
        mock_conn.execute.side_effect = Exception("DB Down")
        
        res = storage.search_fts("query", 10, "snap-1")
        assert res == [] # Should catch and return empty
