import asyncio
import json
from typing import Dict, Any, Generator, List

from code_graph_indexer.embedding.embedder import CodeEmbedder
from code_graph_indexer.graph.builder import KnowledgeGraphBuilder
from code_graph_indexer.models import ChunkNode, ChunkContent, RetrievedContext
from code_graph_indexer.providers.embedding import DummyEmbeddingProvider
from code_graph_indexer.retriever import CodeRetriever


class InMemoryStorage:
    def __init__(self):
        self.nodes: Dict[str, Dict[str, Any]] = {}
        self.contents: Dict[str, str] = {}
        self.search_docs: List[Dict[str, Any]] = []
        self.embeddings: Dict[str, List[float]] = {}
        self.staged: List[Dict[str, Any]] = []
        self.active_snapshots: Dict[str, str] = {}

    def add_nodes(self, nodes):
        for node in nodes:
            data = node.to_dict() if hasattr(node, "to_dict") else node
            self.nodes[data["id"]] = data

    def add_contents(self, contents):
        for content in contents:
            data = content.to_dict() if hasattr(content, "to_dict") else content
            self.contents[data["chunk_hash"]] = data["content"]

    def add_search_index(self, search_docs):
        self.search_docs.extend(search_docs)

    def get_nodes_to_embed(self, snapshot_id: str, model_name: str, batch_size: int = 2000) -> Generator[Dict[str, Any], None, None]:
        for node in self.nodes.values():
            yield {
                "id": node["id"],
                "file_path": node["file_path"],
                "language": "python",
                "category": "code",
                "metadata_json": json.dumps(node.get("metadata", {})),
                "content": self.contents.get(node["chunk_hash"], ""),
                "start_line": node["start_line"],
                "end_line": node["end_line"],
            }

    def prepare_embedding_staging(self):
        self.staged = []

    def load_staging_data(self, data_generator):
        for row in data_generator:
            self.staged.append(
                {
                    "id": row[0],
                    "chunk_id": row[1],
                    "snapshot_id": row[2],
                    "vector_hash": row[3],
                    "file_path": row[4],
                    "language": row[5],
                    "category": row[6],
                    "start_line": row[7],
                    "end_line": row[8],
                    "model_name": row[9],
                    "content": row[10],
                }
            )

    def backfill_staging_vectors(self, snapshot_id: str) -> int:
        return 0

    def flush_staged_hits(self, snapshot_id: str) -> int:
        return 0

    def fetch_staging_delta(self, snapshot_id: str, batch_size: int = 2000):
        yield self.staged

    def save_embeddings_direct(self, records: List[Dict[str, Any]]):
        for record in records:
            self.embeddings[record["chunk_id"]] = record["embedding"]

    def cleanup_staging(self, snapshot_id: str):
        self.staged = []

    def search_vectors(self, query_vector, limit: int, snapshot_id: str, filters=None):
        results = []
        for node_id, node in self.nodes.items():
            if node_id in self.embeddings:
                results.append(
                    {
                        "id": node_id,
                        "file_path": node["file_path"],
                        "content": self.contents.get(node["chunk_hash"], ""),
                        "metadata": json.dumps(node.get("metadata", {})),
                        "start_line": node["start_line"],
                        "end_line": node["end_line"],
                        "repo_id": "repo-1",
                        "branch": "main",
                        "language": "python",
                        "score": 1.0,
                    }
                )
        return results[:limit]

    def search_fts(self, query: str, limit: int, snapshot_id: str, filters=None):
        results = []
        for doc in self.search_docs:
            if query.lower() in doc["content"].lower():
                node = self.nodes[doc["node_id"]]
                results.append(
                    {
                        "id": doc["node_id"],
                        "file_path": doc["file_path"],
                        "content": doc["content"],
                        "metadata": json.dumps(node.get("metadata", {})),
                        "start_line": node["start_line"],
                        "end_line": node["end_line"],
                        "repo_id": "repo-1",
                        "branch": "main",
                        "language": "python",
                        "score": 0.9,
                    }
                )
        return results[:limit]

    def get_active_snapshot_id(self, repository_id: str):
        return self.active_snapshots.get(repository_id)

    def get_context_neighbors(self, node_id: str):
        return {"parents": [{"type": "class", "file_path": "src/app.py", "start_line": 1}], "calls": []}

    def get_neighbor_metadata(self, node_id: str):
        return {"parent": {"label": "Class", "id": "parent-1"}}


def test_workflow_index_embed_retrieve():
    storage = InMemoryStorage()
    builder = KnowledgeGraphBuilder(storage)

    nodes = [
        ChunkNode(
            id="n1",
            file_id="f1",
            file_path="src/app.py",
            chunk_hash="ch1",
            start_line=1,
            end_line=2,
            byte_range=[0, 10],
            metadata={"semantic_matches": [{"label": "Function"}]},
        )
    ]
    contents = [ChunkContent(chunk_hash="ch1", content="print('hello world')")]

    storage.add_nodes(nodes)
    storage.add_contents(contents)
    builder.index_search_content(nodes, {"ch1": contents[0]})

    embedder = CodeEmbedder(storage, DummyEmbeddingProvider(dim=4))

    async def run_embed():
        updates = []
        async for update in embedder.run_indexing("snap-1", batch_size=1, mock_api=True):
            updates.append(update["status"])
        return updates

    statuses = asyncio.run(run_embed())
    assert "completed" in statuses
    assert storage.embeddings

    storage.active_snapshots["repo-1"] = "snap-1"

    retriever = CodeRetriever(storage, DummyEmbeddingProvider(dim=4))
    results = retriever.retrieve("hello", repo_id="repo-1", limit=1)

    assert results
    assert isinstance(results[0], RetrievedContext)
    assert results[0].file_path == "src/app.py"
    assert "Class" in results[0].nav_hints["parent"]["label"]
