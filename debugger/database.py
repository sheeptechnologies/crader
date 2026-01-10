import os

from src.crader.storage.postgres import PostgresGraphStorage

# Default to the one used in tests, but allow env override
DB_URL = os.getenv("SHEEP_DB_URL", "postgresql://sheep_user:sheep_password@localhost:5433/sheep_index")

from src.crader.storage.connector import PooledConnector

_storage_instance = None


def get_storage():
    """
    Returns a singleton instance of the storage.
    """
    global _storage_instance
    if _storage_instance is None:
        # Use PooledConnector as per new library API
        connector = PooledConnector(dsn=DB_URL, min_size=4, max_size=20)
        _storage_instance = PostgresGraphStorage(connector, vector_dim=1536)
    return _storage_instance
