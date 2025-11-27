from abc import ABC, abstractmethod
from typing import List
import random

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