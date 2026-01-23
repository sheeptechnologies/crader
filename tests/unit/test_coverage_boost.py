from unittest.mock import MagicMock, mock_open, patch

import pytest

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

    @patch("crader.parsing.parser.QueryCursor", None)
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
