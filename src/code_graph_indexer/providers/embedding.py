import os
from abc import ABC, abstractmethod
from typing import List
import random
import logging
import openai

logger = logging.getLogger(__name__)

class EmbeddingProvider(ABC):
    """
    Interfaccia astratta per qualsiasi modello di embedding (OpenAI, Voyage, Ollama, etc.)
    """
    
    @abstractmethod
    def embed(self, texts: List[str]) -> List[List[float]]:
        """
        Input: Lista di stringhe (chunk o contesti)
        Output: Lista di vettori (liste di float)
        """
        pass
    
    @property
    @abstractmethod
    def dimension(self) -> int:
        """Dimensione del vettore (es. 1536, 1024, 768)"""
        pass

    @property
    @abstractmethod
    def model_name(self) -> str:
        """Identificativo del modello"""
        pass

class DummyEmbeddingProvider(EmbeddingProvider):
    """
    Provider per test. Restituisce vettori casuali.
    """
    def __init__(self, dim: int = 1536):
        self._dim = dim

    def embed(self, texts: List[str]) -> List[List[float]]:
        # Genera vettori random.
        return [[random.random() for _ in range(self._dim)] for _ in texts]

    @property
    def dimension(self) -> int:
        return self._dim

    @property
    def model_name(self) -> str:
        return "dummy-random-v1"

class FastEmbedProvider(EmbeddingProvider):
    """
    Provider locale ottimizzato per CPU usando la libreria 'fastembed'.
    Default: jinaai/jina-embeddings-v2-base-code (8k context window).
    """
    def __init__(self, model_name: str = "jinaai/jina-embeddings-v2-base-code"):
        try:
            from fastembed import TextEmbedding
        except ImportError:
            raise ImportError(
                "La libreria 'fastembed' non è installata. "
                "Per usare questo provider, installala con: pip install fastembed"
            )
        
        self._model_name = model_name
        logger.info(f"📥 Inizializzazione FastEmbed con modello: {model_name}...")
        
        # L'inizializzazione scarica il modello se non è presente in cache
        self._model = TextEmbedding(model_name=model_name)
        
        # Calcoliamo la dimensione reale facendo un embedding di prova (più sicuro che hardcodare)
        # Jina v2 base è 768, ma se l'utente cambia modello vogliamo che si adatti.
        dummy_vec = list(self._model.embed(["test"]))[0]
        self._dim = len(dummy_vec)
        
        logger.info(f"✅ Modello caricato. Dimensione vettori: {self._dim}")

    def embed(self, texts: List[str]) -> List[List[float]]:
        # FastEmbed gestisce il batching internamente in modo efficiente (ONNX)
        # Ritorna un generatore di numpy arrays, convertiamo in liste di float
        embeddings = list(self._model.embed(texts))
        return [e.tolist() for e in embeddings]

    @property
    def dimension(self) -> int:
        return self._dim

    @property
    def model_name(self) -> str:
        return self._model_name
    

class OpenAIEmbeddingProvider(EmbeddingProvider):
    def __init__(self, model: str = "text-embedding-3-small", batch_size: int = 100):
        self._model = model
        self._batch_size = batch_size
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise ValueError("OPENAI_API_KEY environment variable not set.")
        self.client = openai.Client(api_key=api_key)
        
        # Dimensioni note per evitare chiamate API inutili al setup
        self._dims = {
            "text-embedding-3-small": 1536,
            "text-embedding-3-large": 3072,
            "text-embedding-ada-002": 1536
        }

    @property
    def dimension(self) -> int:
        return self._dims.get(self._model, 1536)

    @property
    def model_name(self) -> int:
        return self._model

    def embed(self, texts: List[str]) -> List[List[float]]:
        """
        Genera embedding usando le API di OpenAI con batching automatico.
        """
        # Pulizia input: OpenAI non accetta stringhe vuote o solo spazi
        clean_texts = [t if t.strip() else "empty" for t in texts]
        
        all_embeddings = []
        
        # Chunking della lista in batch da inviare all'API
        # (OpenAI ha limiti di token per richiesta, 100 chunk è un buon compromesso)
        for i in range(0, len(clean_texts), self._batch_size):
            batch = clean_texts[i : i + self._batch_size]
            try:
                response = self.client.embeddings.create(
                    input=batch,
                    model=self._model
                )
                # Estraiamo i vettori ordinati
                batch_embeddings = [item.embedding for item in response.data]
                all_embeddings.extend(batch_embeddings)
            except Exception as e:
                logger.error(f"OpenAI API Error on batch {i}: {e}")
                # Fallback: ritorna vettori di zeri o rilancia? Meglio rilanciare.
                raise e

        return all_embeddings