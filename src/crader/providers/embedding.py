import asyncio
import logging
import os
import random
from abc import ABC, abstractmethod
from typing import List

import openai
from openai import AsyncOpenAI

logger = logging.getLogger(__name__)


class EmbeddingProvider(ABC):
    """
    Abstract Interface for Vector Embedding Models.

    This contract allows the indexer to be model-agnostic, supporting OpenAI, Cohere, HuggingFace, etc.
    It mandates both synchronous (legacy/CLI) and asynchronous (high-throughput pipeline) support.

    **Enterprise Requirements**:
    *   **Concurrency Control**: Must expose `max_concurrency` to prevent rate-limiting downstream.
    *   **Dimensions**: Must declare vector size explicitly for database schema init.
    """

    @abstractmethod
    def embed(self, texts: List[str]) -> List[List[float]]:
        pass

    @abstractmethod
    async def embed_async(self, texts: List[str]) -> List[List[float]]:
        pass

    @property
    @abstractmethod
    def dimension(self) -> int:
        pass

    @property
    @abstractmethod
    def model_name(self) -> str:
        pass

    @property
    def max_concurrency(self) -> int:
        """
        Recommended maximum number of concurrent requests.

        Example:
        *   OpenAI Tier 1: ~5-10 parallel batches before 429 Too Many Requests.
        *   Local Model (FastEmbed): ~CPU Core count (typically low).
        """
        return 5


class DummyEmbeddingProvider(EmbeddingProvider):
    def __init__(self, dim: int = 1536):
        self._dim = dim

    def embed(self, texts: List[str]) -> List[List[float]]:
        return [[random.random() for _ in range(self._dim)] for _ in texts]

    async def embed_async(self, texts: List[str]) -> List[List[float]]:
        await asyncio.sleep(random.uniform(0.05, 0.2))
        return [[random.random() for _ in range(self._dim)] for _ in texts]

    @property
    def dimension(self) -> int:
        return self._dim

    @property
    def model_name(self) -> str:
        return "dummy-random-v1"

    @property
    def max_concurrency(self) -> int:
        return 50


class FastEmbedProvider(EmbeddingProvider):
    def __init__(self, model_name: str = "jinaai/jina-embeddings-v2-base-code"):
        try:
            from fastembed import TextEmbedding
        except ImportError:
            raise ImportError("Installa fastembed: pip install fastembed")

        self._model_name = model_name
        logger.info(f"📥 Init FastEmbed: {model_name}...")
        self._model = TextEmbedding(model_name=model_name)

        dummy_vec = list(self._model.embed(["test"]))[0]
        self._dim = len(dummy_vec)
        logger.info(f"✅ FastEmbed Ready. Dim: {self._dim}")

    def embed(self, texts: List[str]) -> List[List[float]]:
        embeddings = list(self._model.embed(texts))
        return [e.tolist() for e in embeddings]

    async def embed_async(self, texts: List[str]) -> List[List[float]]:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self.embed, texts)

    @property
    def dimension(self) -> int:
        return self._dim

    @property
    def model_name(self) -> str:
        return self._model_name

    @property
    def max_concurrency(self) -> int:
        return 2  # CPU Bound, teniamo basso


class OpenAIEmbeddingProvider(EmbeddingProvider):
    """
    Enterprise-grade OpenAI Integration.

    Uses `AsyncOpenAI` for non-blocking I/O during heavy indexing jobs.
    Includes built-in handling for:
    *   **Rate Limits**: Basic strategy (errors surface to worker, which retries).
    *   **Token Limits**: Pre-cleaning of input (stripping newlines) as recommended by OpenAI documentation.
    *   **Batching**: Handled by the caller (`CodeEmbedder`), but strictly typed here.
    """

    def __init__(self, model: str = "text-embedding-3-small", batch_size: int = 100, max_concurrency: int = 10):
        self._model = model
        self._batch_size = batch_size
        self._max_concurrency = max_concurrency

        api_key = os.getenv("CRADER_OPENAI_API_KEY") or os.getenv("OPENAI_API_KEY")
        if not api_key:
            logger.warning("⚠️ OPENAI_API_KEY non trovata. Le chiamate falliranno.")

        self.client = openai.Client(api_key=api_key)
        self.async_client = AsyncOpenAI(api_key=api_key)

        self._dims = {"text-embedding-3-small": 1536, "text-embedding-3-large": 3072, "text-embedding-ada-002": 1536}

    @property
    def dimension(self) -> int:
        return self._dims.get(self._model, 1536)

    @property
    def model_name(self) -> str:
        return self._model

    @property
    def max_concurrency(self) -> int:
        return self._max_concurrency

    def embed(self, texts: List[str]) -> List[List[float]]:
        """
        Synchronous wrapper for single-threaded usage (not recommended for bulk indexing).
        Implements chunked batching manually to respect `batch_size`.
        """
        clean_texts = [t.replace("\n", " ") for t in texts]
        clean_texts = [t if t.strip() else "empty" for t in clean_texts]
        all_embeddings = []

        for i in range(0, len(clean_texts), self._batch_size):
            batch = clean_texts[i : i + self._batch_size]
            try:
                response = self.client.embeddings.create(input=batch, model=self._model)
                all_embeddings.extend([item.embedding for item in response.data])
            except Exception as e:
                logger.error(f"OpenAI Sync Error: {e}")
                raise e
        return all_embeddings

    async def embed_async(self, texts: List[str]) -> List[List[float]]:
        """
        High-Throughput Asynchronous Embedding Generation.

        Leverages `httpx` via `AsyncOpenAI` client.
        Note: The caller is responsible for semaphore management using `max_concurrency`.
        """
        # OpenAI recommended cleanup
        clean_texts = [t.replace("\n", " ") for t in texts]

        # PROACTIVE TRUNCATION (Safety Net)
        # Model Limit: 8192 tokens.
        # Approx: 1 token ~= 4 chars. Safe Max Chars ~= 8192 * 3.5 ~= 28,000 chars.
        # We clamp at 25,000 characters to be extremely safe including metadata overhead.
        MAX_CHARS = 25000
        clean_texts = [t[:MAX_CHARS] if len(t) > MAX_CHARS else t for t in clean_texts]

        # Prevent empty strings
        clean_texts = [t if t.strip() else "empty_node_content" for t in clean_texts]

        try:
            # AsyncOpenAI gestisce pool e retries internamente
            response = await self.async_client.embeddings.create(input=clean_texts, model=self._model)
            # Garantiamo l'ordine corretto
            return [item.embedding for item in response.data]

        except openai.RateLimitError:
            logger.error("🛑 OpenAI Rate Limit Hit (429). Riduco temporaneamente la concorrenza.")
            raise
        except openai.BadRequestError as e:
            logger.error(f"❌ OpenAI Bad Request (Token Limit?): {e}")
            raise
        except Exception as e:
            logger.error(f"❌ OpenAI Async Unknown Error: {e}")
            raise
