import pytest

from code_graph_indexer.navigator import CodeNavigator


class FakeStorage:
    def __init__(self):
        self.calls = []

    def get_neighbor_chunk(self, node_id, direction):
        return {"id": node_id, "metadata": "{\"semantic_matches\": [{\"category\": \"role\", \"label\": \"Service\"}]}"}

    def get_context_neighbors(self, node_id):
        return {"parents": [{"id": "p1", "metadata": {"semantic_matches": []}}]}

    def get_incoming_references(self, node_id, limit):
        self.calls.append(("incoming", node_id, limit))
        return [{"id": "c1"}]

    def get_neighbor_metadata(self, node_id):
        return {"parent": {"id": "p1"}}

    def get_outgoing_calls(self, node_id, limit=50):
        self.calls.append(("outgoing", node_id, limit))
        return [{"target_id": "t1", "file": "a.py", "relation": "calls"}]


def test_navigator_read_neighbor_chunk_enriches():
    nav = CodeNavigator(FakeStorage())
    chunk = nav.read_neighbor_chunk("n1", direction="next")
    assert chunk["type"] == "Service"


def test_navigator_read_neighbor_chunk_invalid_direction():
    nav = CodeNavigator(FakeStorage())
    with pytest.raises(ValueError):
        nav.read_neighbor_chunk("n1", direction="sideways")


def test_navigator_read_parent_chunk():
    nav = CodeNavigator(FakeStorage())
    parent = nav.read_parent_chunk("n1")
    assert parent["type"] == "Code Block"


def test_navigator_analysis_and_visualize():
    storage = FakeStorage()
    nav = CodeNavigator(storage)

    assert nav.analyze_impact("n1", limit=5) == [{"id": "c1"}]
    assert nav.analyze_dependencies("n1") == [{"target_id": "t1", "file": "a.py", "relation": "calls"}]

    tree = nav.visualize_pipeline("n1", max_depth=1)
    assert tree["root_node"] == "n1"
    assert "t1" in tree["call_graph"]
