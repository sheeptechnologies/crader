import asyncio
import os

from crader.providers.embedding import DummyEmbeddingProvider, OpenAIEmbeddingProvider


class FakeEmbeddings:
    def __init__(self, data):
        self.data = data


class FakeOpenAIClient:
    def __init__(self):
        self.embeddings = type("Emb", (), {"create": self.embeddings_create})()
        self.calls = []

    def embeddings_create(self, input, model):
        self.calls.append((input, model))
        return FakeEmbeddings([type("Item", (), {"embedding": [0.1, 0.2]})() for _ in input])


class FakeAsyncClient:
    def __init__(self):
        self.calls = []
        self.embeddings = type("Emb", (), {"create": self.embeddings_create})()

    async def embeddings_create(self, input, model):
        self.calls.append((input, model))
        return FakeEmbeddings([type("Item", (), {"embedding": [0.3, 0.4]})() for _ in input])


def test_dummy_embedding_provider_async():
    provider = DummyEmbeddingProvider(dim=3)
    vecs = asyncio.run(provider.embed_async(["a", "b"]))
    assert len(vecs) == 2
    assert len(vecs[0]) == 3


def test_dummy_embedding_provider_sync():
    provider = DummyEmbeddingProvider(dim=2)
    vecs = provider.embed(["a"])
    assert len(vecs[0]) == 2


def test_openai_embedding_provider_sync(monkeypatch):
    os.environ["OPENAI_API_KEY"] = "test"
    provider = OpenAIEmbeddingProvider(model="text-embedding-3-small", batch_size=1)
    fake_client = FakeOpenAIClient()
    provider.client = fake_client

    result = provider.embed(["a\n", ""])
    assert result == [[0.1, 0.2], [0.1, 0.2]]
    assert fake_client.calls[0][0] == ["a "]


def test_openai_embedding_provider_async(monkeypatch):
    os.environ["OPENAI_API_KEY"] = "test"
    provider = OpenAIEmbeddingProvider(model="text-embedding-3-small")
    fake_async = FakeAsyncClient()
    provider.async_client = fake_async

    result = asyncio.run(provider.embed_async(["a\n", " "]))
    assert result == [[0.3, 0.4], [0.3, 0.4]]
    assert fake_async.calls[0][0] == ["a ", "empty"]
