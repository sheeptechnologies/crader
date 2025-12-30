import os
import asyncio
from abc import ABC, abstractmethod
from typing import List
import random
import logging
import openai
from openai import AsyncOpenAI

logger = logging.getLogger(__name__)

class EmbeddingProvider(ABC):
    """
    Interfaccia astratta per modelli di embedding.
    Supporta modalità Sync (legacy) e Async (pipeline enterprise).
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
        Numero massimo di batch paralleli. 
        OpenAI Tier 1 supporta ~5-10 richieste concorrenti prima del 429.
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
        return 2 # CPU Bound, teniamo basso

class OpenAIEmbeddingProvider(EmbeddingProvider):
    """
    Provider Enterprise per OpenAI con supporto nativo Async.
    """
    def __init__(self, model: str = "text-embedding-3-small", batch_size: int = 100, max_concurrency: int = 10):
        self._model = model
        self._batch_size = batch_size
        self._max_concurrency = max_concurrency
        
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            logger.warning("⚠️ OPENAI_API_KEY non trovata. Le chiamate falliranno.")
        
        self.client = openai.Client(api_key=api_key)
        self.async_client = AsyncOpenAI(api_key=api_key)
        
        self._dims = {
            "text-embedding-3-small": 1536,
            "text-embedding-3-large": 3072,
            "text-embedding-ada-002": 1536
        }

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
        """Sync fallback"""
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
        Async implementation per high-throughput indexing.
        """
        # OpenAI raccomanda di sostituire newline con spazi per embedding migliori
        clean_texts = [t.replace("\n", " ") for t in texts]
        # Evitiamo stringhe vuote che causano 400 Bad Request
        clean_texts = [t if t.strip() else "empty" for t in clean_texts]
        
        try:
            # AsyncOpenAI gestisce pool e retries internamente
            response = await self.async_client.embeddings.create(
                input=clean_texts,
                model=self._model
            )
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