from crader import indexer as indexer_module
from crader.models import ChunkContent, ChunkNode, CodeRelation, FileRecord


class FakeParser:
    def __init__(self):
        self.calls = []

    def stream_semantic_chunks(self, file_list):
        self.calls.append(file_list)
        f_rec = FileRecord(
            id="f1",
            snapshot_id="snap",
            commit_hash="c1",
            file_hash="h1",
            path=file_list[0],
            language="python",
            size_bytes=10,
            category="code",
            indexed_at="now",
        )
        node = ChunkNode(
            id="n1",
            file_id="f1",
            file_path=file_list[0],
            chunk_hash="ch1",
            start_line=1,
            end_line=2,
            byte_range=[0, 5],
            metadata={"k": "v"},
        )
        content = ChunkContent(chunk_hash="ch1", content="print()")
        rel = CodeRelation(source_file="a.py", target_file="b.py", relation_type="calls")
        yield f_rec, [node], [content], [rel]


class FakeStorage:
    def __init__(self):
        self.files = []
        self.nodes = []
        self.contents = []
        self.rels = []

    def add_files_raw(self, items):
        self.files.extend(items)

    def add_nodes_raw(self, items):
        self.nodes.extend(items)

    def add_contents_raw(self, items):
        self.contents.extend(items)

    def add_relations_raw(self, items):
        self.rels.extend(items)


def test_chunked_iterable():
    chunks = list(indexer_module._chunked_iterable(range(5), 2))
    assert chunks == [(0, 1), (2, 3), (4,)]


def test_process_and_insert_chunk(monkeypatch):
    indexer_module._worker_parser = FakeParser()
    indexer_module._worker_storage = FakeStorage()

    count, metrics = indexer_module._process_and_insert_chunk(["a.py"], {})

    assert count == 1
    assert metrics == {}
    assert indexer_module._worker_storage.files
    assert indexer_module._worker_storage.nodes
    assert indexer_module._worker_storage.contents
    assert indexer_module._worker_storage.rels


def test_init_worker_process(monkeypatch, tmp_path):
    class DummyParser:
        def __init__(self, repo_path):
            self.repo_path = repo_path
            self.snapshot_id = None
            self.repo_info = {}

    class DummyStorage:
        def __init__(self, connector):
            self.connector = connector

    monkeypatch.setattr(
        indexer_module,
        "TreeSitterRepoParser",
        DummyParser,
    )
    monkeypatch.setattr(
        indexer_module,
        "PostgresGraphStorage",
        DummyStorage,
    )
    monkeypatch.setattr(
        indexer_module,
        "SingleConnector",
        lambda dsn: f"connector:{dsn}",
    )

    indexer_module._init_worker_process(
        worktree_path=str(tmp_path),
        snapshot_id="snap",
        commit_hash="commit",
        repo_url="https://example.com/repo",
        branch="main",
        db_url="postgres://",
        worker_init_fn=None,
    )

    assert indexer_module._worker_parser.snapshot_id == "snap"
    assert indexer_module._worker_storage.connector == "connector:postgres://"
