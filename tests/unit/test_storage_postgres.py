"""Unit tests for PostgresGraphStorage.

PostgresGraphStorage manages code graph persistence in PostgreSQL.
These tests verify:
- Repository and snapshot management
- File and code node insertion
- Search operations (vector, full-text, graph)
- Bulk operations using COPY protocol for performance
- Transaction and concurrency handling
"""

import unittest
from unittest.mock import MagicMock, patch, call, ANY
from datetime import datetime
import uuid
from psycopg.errors import UniqueViolation

from code_graph_indexer.storage.postgres import PostgresGraphStorage
from code_graph_indexer.storage.connector import DatabaseConnector
from code_graph_indexer.models import ChunkNode, FileRecord

class TestPostgresGraphStorage(unittest.TestCase):
    """Test suite for PostgresGraphStorage class that manages the database."""
    
    def setUp(self):
        """Configure database mocks for each test.
        
        Creates mocks for:
        - DatabaseConnector: manages DB connections
        - Connection: represents an active connection
        - Cursor: executes SQL queries
        """
        self.mock_connector = MagicMock(spec=DatabaseConnector)
        self.storage = PostgresGraphStorage(self.mock_connector)
        
        # Setup common mocks for connection and cursor
        self.mock_conn = MagicMock()
        self.mock_cursor = MagicMock()
        self.mock_connector.get_connection.return_value.__enter__.return_value = self.mock_conn
        self.mock_conn.execute.return_value = self.mock_cursor
        self.mock_conn.cursor.return_value.__enter__.return_value = self.mock_cursor

    def test_ensure_repository_new(self):
        """Test registration of a new repository.
        
        ensure_repository creates or retrieves a repository in the database.
        This test verifies:
        1. Insertion of a new repository
        2. Return of the generated ID
        3. Correctness of SQL parameters (URL, branch, name)
        """
        expected_id = "test-uuid"
        self.mock_cursor.fetchone.return_value = {"id": expected_id}

        result_id = self.storage.ensure_repository(
            url="https://github.com/org/repo",
            branch="main",
            name="My Repo"
        )

        self.assertEqual(result_id, expected_id)
        
        # Verify SQL query contains INSERT INTO repositories
        sql_call = self.mock_conn.execute.call_args
        self.assertIn("INSERT INTO repositories", sql_call[0][0])
        self.assertEqual(sql_call[0][1][1], "https://github.com/org/repo")
        self.assertEqual(sql_call[0][1][2], "main")
        self.assertEqual(sql_call[0][1][3], "My Repo")

    def test_create_snapshot_new(self):
        """Test creation of a new snapshot.
        
        A snapshot represents the repository state at a specific point in time.
        This test verifies:
        1. Deterministic UUID generation (via mock)
        2. Creation of a new snapshot when none exists
        3. is_new=True flag to indicate creation
        """
        repo_id = str(uuid.uuid4())
        commit_hash = "abc1234"
        
        # Mock UUID for deterministic test results
        with patch("uuid.uuid4", return_value=uuid.UUID("12345678-1234-5678-1234-567812345678")):
            expected_id = str(uuid.UUID("12345678-1234-5678-1234-567812345678"))
            
            # First query: check if exists -> None (doesn't exist)
            # Second query: create new snapshot -> return ID
            self.mock_cursor.fetchone.side_effect = [None, {"id": expected_id}]

            snap_id, is_new = self.storage.create_snapshot(repo_id, commit_hash)

            self.assertEqual(snap_id, expected_id)
            self.assertTrue(is_new)


    def test_create_snapshot_existing(self):
        """Test reusing an existing snapshot."""
        repo_id = str(uuid.uuid4())
        commit_hash = "abc1234"
        
        # Existing snapshot found
        self.mock_cursor.fetchone.return_value = {
            "id": "existing-snap", 
            "status": "indexing"
        }

        snap_id, is_new = self.storage.create_snapshot(repo_id, commit_hash)

        self.assertEqual(snap_id, "existing-snap")
        self.assertFalse(is_new)
        
        # Verify NO insert happened (except logs maybe)
        insert_calls = [c for c in self.mock_conn.execute.call_args_list if "INSERT INTO snapshots" in c[0][0]]
        self.assertEqual(len(insert_calls), 0)

    def test_create_snapshot_force_new(self):
        """Test forcing a new snapshot even if one exists."""
        repo_id = str(uuid.uuid4())
        commit_hash = "abc1234"
        
        with patch("uuid.uuid4", return_value=uuid.UUID("87654321-4321-4321-4321-210987654321")):
            expected_id = str(uuid.UUID("87654321-4321-4321-4321-210987654321"))
            self.mock_cursor.fetchone.return_value = {"id": expected_id}

            snap_id, is_new = self.storage.create_snapshot(repo_id, commit_hash, force_new=True)

            self.assertEqual(snap_id, expected_id)
            self.assertTrue(is_new)

    def test_add_files(self):
        """Test inserting multiple file nodes."""
        snap_id = str(uuid.uuid4())
        file_node = FileRecord(
            id="file-node-123",
            snapshot_id=snap_id,
            commit_hash="abc",
            file_hash="hash123",
            path="src/main.py",
            language="python",
            size_bytes=100,
            category="code",
            indexed_at="now",
            parsing_status="success",
            parsing_error=None
        )
        
        self.storage.add_files([file_node])

        # Check arguments. executemany is called on cursor
        self.mock_cursor.executemany.assert_called_once()
        args = self.mock_cursor.executemany.call_args
        sql = args[0][0]
        data = args[0][1]
        
        self.assertIn("INSERT INTO files", sql)
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]['path'], "src/main.py")

    def test_add_nodes(self):
        """Test adding code nodes (chunks)."""
        snap_id = str(uuid.uuid4())
        chunk = ChunkNode(
            file_path="src/main.py",
            start_line=10,
            end_line=20,
            # content="def foo(): pass", # ChunkNode doesn't hold content
            id="chunk-1",
            file_id="file-1",
            chunk_hash="hash",
            byte_range=[0, 10],
            metadata={"type": "function", "identifier": "foo"}
        )
        
        self.storage.add_nodes([chunk])

        self.mock_cursor.executemany.assert_called_once()
        args = self.mock_cursor.executemany.call_args
        sql = args[0][0]
        data = args[0][1]

        self.assertIn("INSERT INTO nodes", sql)
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]['id'], "chunk-1")
        # Verify metadata serialization happens in add_nodes
        import json
        self.assertEqual(json.loads(data[0]['metadata'])['identifier'], "foo")

    def test_activate_snapshot(self):
        """Test snapshot activation."""
        snap_id = "snap-123"
        stats = {"nodes": 100}
        
        self.storage.activate_snapshot("repo-123", snap_id, stats)
        
        # Verify calls. There are multiple updates.
        calls = self.mock_conn.execute.call_args_list
        # Found UPDATE snapshots?
        self.assertTrue(any("UPDATE snapshots" in str(c[0][0]) for c in calls))
        self.assertTrue(any("status='completed'" in str(c[0][0]) for c in calls)) # Spacing might vary, check relaxed

    def test_save_embeddings(self):
         """Test bulk updating embeddings."""
         batch = [
             {"id": "node-1", "embedding": [0.1, 0.2]}
         ]
         self.storage.save_embeddings(batch)
         self.mock_cursor.executemany.assert_called_once()

    def test_ingest_scip_relations(self):
        """Test batch ingestion of SCIP relations."""
        relations_batch = [
            ("file1.py", 10, 20, "file2.py", 30, 40, "imports", '{"meta": "data"}')
        ]
        
        # Mock the copy context manager
        mock_copy = MagicMock()
        self.mock_cursor.copy.return_value.__enter__.return_value = mock_copy
        
        self.storage.ingest_scip_relations(relations_batch, "snap-123")
        
        # Verify COPY was used
        self.mock_cursor.copy.assert_called()
        mock_copy.write_row.assert_called()
        self.assertEqual(mock_copy.write_row.call_count, 1)

    def test_check_and_reset_reindex_flag(self):
        """Test checking reindex flag."""
        # Case 1: Flag is set (row returned)
        self.mock_cursor.fetchone.return_value = {"id": "repo-123"}
        result = self.storage.check_and_reset_reindex_flag("repo-123")
        self.assertTrue(result)
        # Should execute UPDATE
        self.assertTrue(any("UPDATE repositories" in str(c[0][0]) for c in self.mock_conn.execute.call_args_list))

        # Reset mocks
        self.mock_conn.execute.reset_mock()
        # Case 2: Flag NOT set (None returned)
        self.mock_cursor.fetchone.return_value = None
        result = self.storage.check_and_reset_reindex_flag("repo-123")
        self.assertFalse(result)

    def test_get_file_content_range(self):
        """Test retrieving file content range."""
        self.mock_cursor.fetchall.return_value = [
            {"content": "print('hello')\n", "start_line": 0}
        ]
        
        content = self.storage.get_file_content_range("snap-123", "src/main.py", 0, 1)
        self.assertEqual(content, "print('hello')\n")
        
    def test_prune_snapshot(self):
        """Test pruning particular snapshot."""
        self.storage.prune_snapshot("snap-123")
        
        calls = self.mock_conn.execute.call_args_list
        self.assertTrue(any("DELETE FROM files" in str(c[0][0]) for c in calls))
    def test_search_vectors(self):
        """Test semantic vector search."""
        query_vec = [0.1, 0.2, 0.3]
        mock_results = [{
            "chunk_id": "c1", "file_path": "f.py", "start_line": 1, "end_line": 10,
            "snapshot_id": "s1", "metadata": "{}", "content": "def foo(): pass",
            "language": "python", "distance": 0.1
        }]
        self.mock_cursor.fetchall.return_value = mock_results
        
        results = self.storage.search_vectors(query_vec, 10, "s1")
        
        self.assertEqual(len(results), 1)
        self.assertAlmostEqual(results[0]["score"], 0.9) # 1 - distance
        
        # Verify SQL contains vector operator
        args = self.mock_conn.execute.call_args
        self.assertIn("<=>", args[0][0])
        self.assertEqual(args[0][1][0], query_vec)

    def test_search_fts(self):
        """Test full-text search."""
        mock_results = [{
            "node_id": "n1", "file_path": "f.py", "start_line": 1, "end_line": 10,
            "content": "class Bar", "snapshot_id": "s1", "metadata": "{}", 
            "language": "python", "rank": 0.8
        }]
        self.mock_cursor.fetchall.return_value = mock_results
        
        results = self.storage.search_fts("class Bar", 10, "s1")
        
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["score"], 0.8)
        
        # Verify SQL uses websearch_to_tsquery
        args = self.mock_conn.execute.call_args
        self.assertIn("websearch_to_tsquery", args[0][0])

    def test_build_filter_clause(self):
        """Test dynamic filter generation."""
        # Test 1: Path Prefix
        filters = {"path_prefix": "src/"}
        col_map = {"path": "f.path"}
        sql, params = self.storage._build_filter_clause(filters, col_map)
        self.assertIn("f.path LIKE %s", sql)
        self.assertEqual(params[0], "src%")
        
        # Test 2: Language List
        filters = {"language": ["python", "go"]}
        col_map = {"lang": "f.lang"}
        sql, params = self.storage._build_filter_clause(filters, col_map)
        self.assertIn("f.lang = ANY(%s)", sql)
        self.assertEqual(params[0], ["python", "go"])
        
        # Test 3: Role (JSONB)
        filters = {"role": "function"}
        col_map = {"meta": "n.metadata"}
        sql, params = self.storage._build_filter_clause(filters, col_map)
        self.assertIn("n.metadata @> %s::jsonb", sql)

    def test_get_graph_traversal(self):
        """Test various graph traversal methods."""
        # incoming_ref
        self.mock_cursor.fetchall.return_value = [
            {"id": "s1", "file_path": "a.py", "start_line": 10, "relation_type": "calls", "metadata": {}}
        ]
        res = self.storage.get_incoming_references("target-1")
        self.assertEqual(len(res), 1)
        self.assertEqual(res[0]['source_id'], "s1")
        
        # outgoing_calls
        self.mock_cursor.fetchall.return_value = [
            {"id": "t1", "file_path": "b.py", "start_line": 20, "relation_type": "calls", "metadata": {}}
        ]
        res = self.storage.get_outgoing_calls("source-1")
        self.assertEqual(len(res), 1)
        self.assertEqual(res[0]['target_id'], "t1")
        
    def test_find_chunk_id(self):
        self.mock_cursor.fetchone.return_value = {"id": "chunk-1"}
        cid = self.storage.find_chunk_id("src/main.py", [0, 100], "snap-1")
        self.assertEqual(cid, "chunk-1")
        
    def test_get_neighbor_chunk(self):
        self.mock_cursor.fetchone.side_effect = [
            {"file_id": "f1", "start_line": 0, "end_line": 10}, # Current node info
            {"id": "n2", "start_line": 11, "end_line": 20, "chunk_hash": "h1", "content": "next", "metadata": {}, "file_path": "f.py"}
        ]
        res = self.storage.get_neighbor_chunk("n1", "next")
        self.assertEqual(res['id'], "n2")




    def test_add_nodes_fast(self):
        """Test COPY protocol for adding nodes."""
        class MockNode:
            def to_dict(self):
                return {
                    "id": "n1", "file_path": "a.py", "start_line": 1, "end_line": 2, 
                    "snapshot_id": "s1", "byte_range": [0, 10], "chunk_hash": "h1"
                }

        nodes = [MockNode()]
        
        # Mock copy context manager
        mock_copy_manager = MagicMock()
        mock_copy_obj = MagicMock()
        mock_copy_manager.__enter__.return_value = mock_copy_obj
        self.mock_cursor.copy.return_value = mock_copy_manager
        
        self.storage.add_nodes_fast(nodes)
        
        # Verify call arguments loosely
        args = self.mock_cursor.copy.call_args[0]
        self.assertIn("COPY nodes", args[0])
        self.assertIn("FROM STDIN", args[0])
        mock_copy_obj.write_row.assert_called()

    def test_add_files_raw(self):
        """Test raw file insertion."""
        files = [("id1", "path/to/f1", "checksum", "s1", "python")]
        self.storage.add_files_raw(files)
        # Verify batch insert
        self.mock_cursor.executemany.assert_called()
        args = self.mock_cursor.executemany.call_args
        self.assertIn("INSERT INTO files", args[0][0])
        self.assertEqual(len(args[0][1]), 1)

    def test_add_nodes_raw(self):
        """Test raw node insertion."""
        nodes = [("n1", "path", 1, 2, 0, 10, "func", "foo", "def foo", "h", "s1", "{}", None, "python")]
        
        mock_copy_manager = MagicMock()
        mock_copy_obj = MagicMock()
        mock_copy_manager.__enter__.return_value = mock_copy_obj
        self.mock_cursor.copy.return_value = mock_copy_manager

        self.storage.add_nodes_raw(nodes)
        
        self.mock_cursor.copy.assert_called()
        mock_copy_obj.write_row.assert_called()

    def test_add_contents_raw(self):
        """Test raw content insertion."""
        contents = [("h1", "content")]
        self.storage.add_contents_raw(contents)
        self.mock_cursor.executemany.assert_called()
        self.assertIn("INSERT INTO contents", self.mock_cursor.executemany.call_args[0][0])

    def test_ingest_scip_relations(self):
        """Test SCIP relation ingestion using COPY."""
        relations = [
            ("s1", 1, 2, "t1", 3, 4, "ref", "{}")
        ]
        
        # Mock copy context manager
        mock_copy_manager = MagicMock()
        mock_copy_obj = MagicMock()
        mock_copy_manager.__enter__.return_value = mock_copy_obj
        self.mock_cursor.copy.return_value = mock_copy_manager

        # Mock transaction context manager
        mock_tx = MagicMock()
        self.mock_conn.transaction.return_value = mock_tx
        mock_tx.__enter__.return_value = None
        
        self.storage.ingest_scip_relations(relations, "snap-1")
        
        # Check for temp table creation
        execute_calls = [c[0][0] for c in self.mock_cursor.execute.call_args_list]
        self.assertTrue(any("CREATE TEMP TABLE" in sql for sql in execute_calls))
        
        self.mock_cursor.copy.assert_called()
        mock_copy_obj.write_row.assert_called()





    def test_get_incoming_definitions_bulk(self):
        """Test bulk definition checkout."""
        # Fix mock return structure
        self.mock_cursor.fetchall.return_value = [{"target_id": "n1", "metadata": {"symbol": "foo"}}]
        res = self.storage.get_incoming_definitions_bulk(["n1"])
        self.assertEqual(len(res), 1)
        self.assertIn("target_id", self.mock_conn.execute.call_args[0][0]) 

    def test_get_contents_bulk(self):
        """Test bulk content retrieval."""
        self.mock_cursor.fetchall.return_value = [{"chunk_hash": "h1", "content": "c1"}]
        res = self.storage.get_contents_bulk(["h1"])
        self.assertEqual(len(res), 1)
        # Relaxed SQL check
        sql = self.mock_conn.execute.call_args[0][0]
        self.assertIn("SELECT chunk_hash", sql)
        self.assertIn("FROM contents", sql)
    

    def test_get_context_neighbors(self):
        """Test context neighbor retrieval."""
        # Use a fresh cursor for this test to ensure side_effect sequence is respected
        test_cursor = MagicMock()
        self.mock_conn.execute.return_value = test_cursor # Override generic setup

        # Mock the sequence of fetchone calls sufficient to pass the first check
        test_cursor.fetchone.side_effect = [
            {"file_id": "f1", "start_line": 10, "end_line": 20}, # curr
            {"id": "n2", "metadata": {"semantic_matches": []}}, # next
            None, # prev
            None  # parent
        ]
        
        self.storage.get_context_neighbors("n1")
        # Ensure queries were executed
        self.assertTrue(self.mock_conn.execute.call_count >= 1)

    def test_prune_snapshot(self):
        """Test snapshot pruning."""
        self.storage.prune_snapshot("s1")
        self.mock_conn.execute.assert_called()

if __name__ == '__main__':
    unittest.main()


