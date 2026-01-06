import asyncio

from code_graph_indexer.embedding.embedder import _compute_prompt_and_hash, _prepare_batch_for_staging, CodeEmbedder
from code_graph_indexer.providers.embedding import DummyEmbeddingProvider


class FakeStorage:
    def __init__(self):
        self.staged = []
        self.saved = []
        self.cleaned = []

    def prepare_embedding_staging(self):
        self.prepared = True

    def get_nodes_to_embed(self, snapshot_id, model_name, batch_size=2000):
        yield {
            "id": "n1",
            "file_path": "a.py",
            "language": "python",
            "category": "code",
            "metadata_json": "{\"semantic_matches\": [{\"category\": \"role\", \"label\": \"Class\"}]}",
            "content": "print('x')",
        }

    def load_staging_data(self, data_generator):
        self.staged.extend(list(data_generator))

    def backfill_staging_vectors(self, snapshot_id):
        return 0

    def flush_staged_hits(self, snapshot_id):
        return 0

    def fetch_staging_delta(self, snapshot_id, batch_size=2000):
        yield [
            {
                "id": "v1",
                "chunk_id": "n1",
                "vector_hash": "hash",
                "content": "hello",
                "file_path": "a.py",
                "language": "python",
                "category": "code",
                "start_line": 1,
                "end_line": 2,
            }
        ]

    def save_embeddings_direct(self, records):
        self.saved.extend(records)

    def cleanup_staging(self, snapshot_id):
        self.cleaned.append(snapshot_id)


def test_compute_prompt_and_hash_deterministic():
    text, v_hash = _compute_prompt_and_hash(
        {
            "file_path": "a.py",
            "language": "python",
            "category": "code",
            "metadata_json": "{\"semantic_matches\": [{\"category\": \"role\", \"label\": \"Class\"}]}",
            "content": "print('x')",
            "incoming_definitions": ["Foo"],
        }
    )
    assert "Role: Class" in text
    assert "Defines: Foo" in text
    assert len(v_hash) == 64


def test_prepare_batch_for_staging():
    rows = _prepare_batch_for_staging(
        [{"id": "n1", "file_path": "a.py", "content": "x"}],
        model_name="model",
        snapshot_id="snap",
    )
    assert rows[0][2] == "snap"
    assert rows[0][9] == "model"


def test_code_embedder_run_indexing():
    storage = FakeStorage()
    provider = DummyEmbeddingProvider(dim=2)
    embedder = CodeEmbedder(storage, provider)

    async def run():
        updates = []
        async for update in embedder.run_indexing("snap", batch_size=1, mock_api=True):
            updates.append(update["status"])
        return updates

    statuses = asyncio.run(run())
    assert "staging_complete" in statuses
    assert "completed" in statuses
    assert storage.saved
    assert storage.cleaned == ["snap"]
