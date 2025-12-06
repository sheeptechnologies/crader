import os
from src.code_graph_indexer.storage.postgres import PostgresGraphStorage

# Default to the one used in tests, but allow env override
DB_URL = os.getenv("SHEEP_DB_URL", "postgresql://sheep_user:sheep_password@localhost:5433/sheep_index")

def get_storage():
    """
    Returns a singleton-like instance of the storage.
    """
    return PostgresGraphStorage(DB_URL, vector_dim=1536)
