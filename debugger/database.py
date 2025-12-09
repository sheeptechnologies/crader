import os
from src.code_graph_indexer.storage.postgres import PostgresGraphStorage

# Default to the one used in tests, but allow env override
DB_URL = os.getenv("SHEEP_DB_URL", "postgresql://sheep_user:sheep_password@localhost:5433/sheep_index")

import psycopg

_storage_instance = None

def get_storage():
    """
    Returns a singleton instance of the storage.
    """
    global _storage_instance
    if _storage_instance is None:
        _storage_instance = PostgresGraphStorage(DB_URL, vector_dim=1536, timeout=60.0)
    return _storage_instance
