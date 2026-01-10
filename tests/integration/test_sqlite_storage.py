import os
import uuid

from crader.storage.sqlite import SqliteGraphStorage


class SqliteStorageHarness(SqliteGraphStorage):
    __test__ = False
    def ensure_repository(self, url: str, branch: str, name: str) -> str:
        return self.register_repository(str(uuid.uuid4()), name, url, branch, "c1")

    def create_snapshot(self, repository_id: str, commit_hash: str):
        return str(uuid.uuid4()), True

    def activate_snapshot(self, repository_id: str, snapshot_id: str, stats=None):
        return None

    def get_snapshot_manifest(self, snapshot_id: str):
        return {}

    def get_file_content_range(self, snapshot_id: str, file_path: str, start_line=None, end_line=None):
        return None

    def fail_snapshot(self, snapshot_id: str, error: str):
        return None

    def prune_snapshot(self, snapshot_id: str):
        return None

    def get_active_snapshot_id(self, repository_id: str):
        return None

    def get_neighbor_metadata(self, node_id: str):
        return {}

    def prepare_embedding_staging(self):
        return None

    def load_staging_data(self, data_generator):
        return None

    def backfill_staging_vectors(self, snapshot_id: str):
        return 0

    def flush_staged_hits(self, snapshot_id: str):
        return 0

    def fetch_staging_delta(self, snapshot_id: str, batch_size: int = 2000):
        return iter(())

    def save_embeddings_direct(self, records):
        return None


def test_sqlite_storage_search_and_content(tmp_path):
    db_path = tmp_path / "test.db"
    storage = SqliteStorageHarness(str(db_path))
    try:
        repo_id = storage.register_repository(
            id=str(uuid.uuid4()),
            name="repo",
            url="local://repo",
            branch="main",
            commit_hash="c1",
            local_path=str(tmp_path),
        )

        file_id = str(uuid.uuid4())
        storage.add_files(
            [
                {
                    "id": file_id,
                    "repo_id": repo_id,
                    "commit_hash": "c1",
                    "file_hash": "hash",
                    "path": "src/app.py",
                    "language": "python",
                    "size_bytes": 10,
                    "category": "code",
                    "indexed_at": "now",
                }
            ]
        )

        node_id = str(uuid.uuid4())
        storage.add_nodes(
            [
                {
                    "id": node_id,
                    "file_id": file_id,
                    "file_path": "src/app.py",
                    "chunk_hash": "ch1",
                    "start_line": 1,
                    "end_line": 1,
                    "byte_range": [0, 10],
                    "metadata": {"semantic_matches": []},
                }
            ]
        )

        storage.add_contents([
            {"chunk_hash": "ch1", "content": "print('hello')"}
        ])

        storage.add_search_index(
            [
                {
                    "node_id": node_id,
                    "file_path": "src/app.py",
                    "tags": "hello",
                    "content": "print('hello')",
                }
            ]
        )

        results = storage.search_fts("hello", limit=5, repo_id=repo_id, branch="main")
        assert results
        assert results[0]["file_path"] == "src/app.py"

        contents = storage.get_contents_bulk(["ch1"])
        assert contents["ch1"] == "print('hello')"
    finally:
        storage.close()
