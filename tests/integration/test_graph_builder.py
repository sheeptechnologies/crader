from code_graph_indexer.graph.builder import KnowledgeGraphBuilder
from code_graph_indexer.models import ChunkNode, ChunkContent, CodeRelation


class FakeStorage:
    def __init__(self):
        self.search_docs = []
        self.edges = []
        self.find_calls = []

    def add_files(self, files):
        self.files = files

    def add_nodes(self, nodes):
        self.nodes = nodes

    def add_contents(self, contents):
        self.contents = contents

    def add_search_index(self, search_batch):
        self.search_docs.extend(search_batch)

    def find_chunk_id(self, file_path, byte_range, snapshot_id):
        self.find_calls.append((file_path, tuple(byte_range), snapshot_id))
        return f"{file_path}:{byte_range[0]}"

    def add_edge(self, source_id, target_id, relation_type, metadata):
        self.edges.append((source_id, target_id, relation_type, metadata))

    def get_stats(self):
        return {"nodes": 1}


def test_index_search_content_builds_tags():
    storage = FakeStorage()
    builder = KnowledgeGraphBuilder(storage)
    nodes = [
        ChunkNode(
            id="n1",
            file_id="f1",
            file_path="a.py",
            chunk_hash="h1",
            start_line=1,
            end_line=2,
            byte_range=[0, 10],
            metadata={
                "semantic_matches": [
                    {"value": "Auth", "label": "Auth Handler"},
                    {"label": "Controller"},
                ]
            },
        )
    ]
    contents = {"h1": ChunkContent(chunk_hash="h1", content="print('x')")}

    builder.index_search_content(nodes, contents)

    assert storage.search_docs[0]["tags"] == "auth controller handler"


def test_add_relations_resolves_ids_and_skips_self():
    storage = FakeStorage()
    builder = KnowledgeGraphBuilder(storage)
    rels = [
        CodeRelation(
            source_file="a.py",
            target_file="b.py",
            relation_type="calls",
            source_byte_range=[0, 1],
            target_byte_range=[2, 3],
            metadata={},
        ),
        CodeRelation(
            source_file="a.py",
            target_file="ext",
            relation_type="imports",
            source_byte_range=[0, 1],
            target_byte_range=None,
            metadata={"is_external": True},
        ),
        CodeRelation(
            source_file="a.py",
            target_file="a.py",
            relation_type="calls",
            source_byte_range=[0, 1],
            target_byte_range=[0, 1],
            metadata={},
        ),
    ]

    builder.add_relations(rels, snapshot_id="snap")

    assert any(edge[2] == "calls" for edge in storage.edges)
    assert any(edge[2] == "imports" for edge in storage.edges)
    # self relation should be skipped
    assert len(storage.edges) == 2


def test_builder_get_stats():
    storage = FakeStorage()
    builder = KnowledgeGraphBuilder(storage)
    assert builder.get_stats() == {"nodes": 1}
