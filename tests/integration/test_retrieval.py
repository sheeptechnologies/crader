import json

import pytest

from crader.models import RetrievedContext
from crader.retrieval.graph_walker import GraphWalker
from crader.retrieval.rankers import reciprocal_rank_fusion
from crader.retrieval.searcher import SearchExecutor
from crader.retriever import CodeRetriever


class FakeStorage:
    def __init__(self):
        self.vector_calls = []
        self.fts_calls = []
        self.neighbors_calls = []
        self.nav_calls = []

    def search_vectors(self, query_vector, limit, snapshot_id, filters=None):
        self.vector_calls.append((query_vector, limit, snapshot_id, filters))
        return [{"id": "n1", "score": 0.5}]

    def search_fts(self, query, limit, snapshot_id, filters=None):
        self.fts_calls.append((query, limit, snapshot_id, filters))
        return [{"id": "n2", "score": 0.7}]

    def get_context_neighbors(self, node_id):
        self.neighbors_calls.append(node_id)
        return {
            "parents": [{"type": "class", "file_path": "a.py", "start_line": 1}],
            "calls": [{"symbol": "foo"}, {"symbol": "foo"}, {"symbol": "<unknown>"}],
        }

    def get_neighbor_metadata(self, node_id):
        self.nav_calls.append(node_id)
        return {"parent": {"label": "Parent", "id": "p1"}}

    def get_active_snapshot_id(self, repo_id):
        return "snap123"


class FakeEmbedder:
    def __init__(self):
        self.calls = []

    def embed(self, texts):
        self.calls.append(texts)
        return [[0.1, 0.2]]


def test_reciprocal_rank_fusion_scores():
    candidates = {
        "a": {"rrf_ranks": {"vector": 0}},
        "b": {"rrf_ranks": {"vector": 1, "keyword": 0}},
    }
    ranked = reciprocal_rank_fusion(candidates, k=1)
    assert ranked[0]["final_rrf_score"] >= ranked[1]["final_rrf_score"]


def test_search_executor_accumulates():
    storage = FakeStorage()
    embedder = FakeEmbedder()
    candidates = {}

    SearchExecutor.vector_search(storage, embedder, "query", 5, "snap", candidates=candidates)
    SearchExecutor.keyword_search(storage, "query", 5, "snap", candidates=candidates)

    assert "n1" in candidates and "n2" in candidates
    assert candidates["n1"]["rrf_ranks"]["vector"] == 0
    assert candidates["n2"]["rrf_ranks"]["keyword"] == 0


def test_search_executor_handles_error(monkeypatch):
    storage = FakeStorage()

    def explode(*_args, **_kwargs):
        raise RuntimeError("fail")

    monkeypatch.setattr(storage, "search_vectors", explode)
    SearchExecutor.vector_search(storage, FakeEmbedder(), "query", 5, "snap", candidates={})


def test_graph_walker_expand_context():
    storage = FakeStorage()
    walker = GraphWalker(storage)
    ctx = walker.expand_context({"id": "n1"})
    assert ctx["parent_context"] == "Inside class defined in a.py (L1)"
    assert ctx["outgoing_definitions"] == ["foo"]


def test_graph_walker_module_parent_returns_none():
    storage = FakeStorage()

    def module_neighbors(_node_id):
        return {"parents": [{"type": "module", "file_path": "a.py", "start_line": 1}], "calls": []}

    storage.get_context_neighbors = module_neighbors
    walker = GraphWalker(storage)
    ctx = walker.expand_context({"id": "n1"})
    assert ctx["parent_context"] is None


def test_code_retriever_hybrid_flow():
    storage = FakeStorage()
    retriever = CodeRetriever(storage, FakeEmbedder())

    [
        {
            "id": "n1",
            "file_path": "a.py",
            "content": "x",
            "metadata": json.dumps({"semantic_matches": [{"label": "Class"}]}),
            "methods": {"vector"},
            "final_rrf_score": 1.0,
        }
    ]

    def fake_build_response(docs, snapshot_id):
        return [RetrievedContext(node_id=docs[0]["id"], file_path="a.py", content="x")]

    retriever._build_response = fake_build_response

    results = retriever.retrieve("query", repo_id="repo", limit=1)
    assert results and results[0].node_id == "n1"


def test_code_retriever_requires_repo_or_snapshot():
    storage = FakeStorage()
    retriever = CodeRetriever(storage, FakeEmbedder())
    with pytest.raises(ValueError):
        retriever.retrieve("query", repo_id=None, snapshot_id=None)


def test_code_retriever_handles_missing_snapshot(monkeypatch):
    storage = FakeStorage()
    retriever = CodeRetriever(storage, FakeEmbedder())
    monkeypatch.setattr(storage, "get_active_snapshot_id", lambda _repo_id: None)
    assert retriever.retrieve("query", repo_id="repo") == []
