import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import crader.indexer
from crader.indexer import CodebaseIndexer
from crader.providers.embedding import EmbeddingProvider


class TestCodebaseIndexerLifecycle(unittest.TestCase):
    def setUp(self):
        # Manual Patching for indexer module
        self.pg_patcher = patch("crader.storage.postgres.PostgresGraphStorage")
        self.mock_storage_cls = self.pg_patcher.start()

        # INJECT into indexer namespace manually
        self.original_pg = getattr(crader.indexer, "PostgresGraphStorage", None)
        crader.indexer.PostgresGraphStorage = self.mock_storage_cls

        # Patch others
        self.vm_patcher = patch("crader.indexer.GitVolumeManager")
        self.mock_vm_cls = self.vm_patcher.start()

        # Patch PooledConnector (DatabaseConnector is not imported)
        self.conn_patcher = patch("crader.indexer.PooledConnector")
        self.mock_connector = self.conn_patcher.start()

        self.parser_patcher = patch("crader.indexer.TreeSitterRepoParser")
        self.mock_parser_cls = self.parser_patcher.start()

        # Patch Executors to prevent hanging
        self.ppe_patcher = patch("crader.indexer.concurrent.futures.ProcessPoolExecutor")
        self.mock_ppe = self.ppe_patcher.start()

        # Patch as_completed because it hangs on Mocks
        self.as_completed_patcher = patch("crader.indexer.concurrent.futures.as_completed")
        self.mock_as_completed = self.as_completed_patcher.start()
        self.mock_as_completed.side_effect = lambda futures: list(futures)

    def tearDown(self):
        self.pg_patcher.stop()
        if self.original_pg:
            crader.indexer.PostgresGraphStorage = self.original_pg
        self.vm_patcher.stop()
        self.conn_patcher.stop()
        self.parser_patcher.stop()
        self.ppe_patcher.stop()
        self.as_completed_patcher.stop()

    def test_initialization(self):
        """Test correct initialization of components."""
        CodebaseIndexer("http://repo", "main", "postgres://...")

        self.mock_vm_cls.assert_called_with()
        self.mock_storage_cls.assert_called()

    def test_index_workflow_success(self):
        """Test the full indexing orchestration (happy path)."""
        mock_storage = self.mock_storage_cls.return_value
        indexer = CodebaseIndexer("http://repo", "main", "db_url")

        mock_storage.ensure_repository.return_value = "repo-123"
        mock_storage.create_snapshot.return_value = ("snap-1", True)
        # Fix infinite loop: explicitly return False for this check
        mock_storage.check_and_reset_reindex_flag.return_value = False

        mock_executor_instance = self.mock_ppe.return_value.__enter__.return_value

        mock_future = MagicMock()
        mock_future.result.return_value = (1, [])
        mock_executor_instance.submit.return_value = mock_future

        with patch("os.walk", return_value=[("/tmp", [], ["file.py"])]):
            snap_id = indexer.index()

        self.assertEqual(snap_id, "snap-1")
        mock_storage.activate_snapshot.assert_called()

    def test_index_skip_existing(self):
        """Test that indexing is skipped if snapshot exists."""
        mock_storage = self.mock_storage_cls.return_value
        indexer = CodebaseIndexer("http://repo", "main", "db_url")

        mock_storage.create_snapshot.return_value = ("existing-snap", False)
        mock_storage.ensure_repository.return_value = "repo-123"
        mock_storage.get_active_snapshot_id.return_value = "existing-snap"

        snap_id = indexer.index()

        self.assertEqual(snap_id, "existing-snap")
        # Ensure repo updated IS called (to check commit)
        self.mock_vm_cls.return_value.ensure_repo_updated.assert_called()

    def test_embed_pipeline(self):
        """Test the async embedding pipeline."""
        asyncio.run(self._test_embed_pipeline_async())

    async def _test_embed_pipeline_async(self):
        mock_storage = self.mock_storage_cls.return_value
        indexer = CodebaseIndexer("http://repo", "main", "db_url")

        mock_storage.save_embeddings_direct = MagicMock()

        mock_storage.fetch_staging_delta.return_value = [
            [{"id": 1, "content": "code", "chunk_id": "c1", "vector_hash": "vh"}]
        ]

        mock_provider = MagicMock(spec=EmbeddingProvider)
        mock_provider.max_concurrency = 4
        mock_provider.max_batch_size = 100
        mock_provider.model_name = "test-model"

        mock_provider.embed_async = AsyncMock(return_value=[[0.1, 0.2]])

        updates = []
        async for update in indexer.embed(mock_provider):
            updates.append(update)

        self.assertTrue(len(updates) > 0)

        if mock_storage.save_embeddings_direct.called:
            mock_storage.save_embeddings_direct.assert_called()
        else:
            mock_storage.save_embeddings.assert_called()


if __name__ == "__main__":
    unittest.main()
