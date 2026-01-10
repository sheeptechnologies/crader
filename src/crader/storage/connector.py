import contextlib
import logging
from typing import Any, Generator, Protocol

import psycopg
from pgvector.psycopg import register_vector
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

logger = logging.getLogger(__name__)


class DatabaseConnector(Protocol):
    """
    Interface Contract for PostgreSQL connection providers.

    This protocol ensures that the Persistence Layer (`GraphStorage`) operates agnostically regarding
    how connections are acquired (e.g., via a Threaded Pool, a Single Process setup, or a Serverless connection).
    """

    def get_connection(self) -> Generator[psycopg.Connection, None, None]:
        """
        Yields a valid, active `psycopg.Connection` context.
        The implementation must handle lifecycle management (e.g., checking liveness, returning to pool vs closing).
        """
        ...

    def close(self):
        """Releases all underlying resources (pools, sockets)."""
        ...


class PooledConnector:
    """
    Client-Side Connection Pooler implementation (via `psycopg_pool`).

    **Use Case**:
    Designed for the **Main API Process** or **Orchestrator**, where multiple concurrent threads (e.g., Flask/FastAPI requests)
    need to perform short, frequent queries.

    **Key Features**:
    *   **Auto-Scaling**: Maintains `min_size` connections and scales up to `max_size` under load.
    *   **Vector Support**: Automatically registers `pgvector` codecs on new connections.
    *   **Resilience**: Blocks until a connection is available or the pool is ready.
    """

    def __init__(self, dsn: str, min_size: int = 4, max_size: int = 20):
        """
        Initializes the connection pool.

        Args:
            dsn (str): Libpq connection string (postgres://user:pass@host:port/db).
            min_size (int): Minimum idle connections to keep open.
            max_size (int): usage cap to prevent exhausting DB max_connections.
        """
        self._dsn = dsn
        self.pool = ConnectionPool(
            conninfo=dsn,
            min_size=min_size,
            max_size=max_size,
            kwargs={"row_factory": dict_row, "autocommit": True},
            configure=self._configure,
        )
        # Block until at least one connection is established to ensure system readiness ("Fail Fast")
        self.pool.wait()

    def _configure(self, conn: psycopg.Connection):
        """Callback to configure every new connection in the pool (e.g., ensure pgvector is loaded)."""
        try:
            register_vector(conn)
        except psycopg.ProgrammingError:
            pass

    @contextlib.contextmanager
    def get_connection(self):
        """
        Borrows a connection from the pool contextually.
        Automatically returns it to the pool on exit.
        """
        with self.pool.connection() as conn:
            yield conn

    def close(self):
        """Gracefully shuts down the pool, closing all open sockets."""
        self.pool.close()


class SingleConnector:
    """
    Persistent Single-Connection implementation.

    **Use Case**:
    Designed for **Worker Processes** (multiprocessing spawn) that live to execute long-running,
    sequential batch operations (like `COPY` ingestion). Avoids the overhead and complexity of a pool
    inside a single-threaded independent worker.

    **Stability Features**:
    *   **Auto-Reconnect**: Detects broken pipes/closed sockets and transparently reconnects before yielding.
    """

    def __init__(self, dsn: str):
        self.dsn = dsn
        self.conn: Any = None
        self._connect()

    def _connect(self):
        """Opens a direct TCP socket to PostgreSQL and registers extensions."""
        self.conn = psycopg.connect(self.dsn, autocommit=True, row_factory=dict_row)
        try:
            register_vector(self.conn)
        except psycopg.ProgrammingError:
            pass

    @contextlib.contextmanager
    def get_connection(self):
        """
        Yields the persistent connection.
        Performs a liveness check and reconnects if necessary to handle network blips or PgBouncer timeouts.
        """
        if self.conn.closed:
            logger.warning("⚠️ SingleConnector: Connection lost. Reconnecting...")
            self._connect()

        yield self.conn

    def close(self):
        if self.conn and not self.conn.closed:
            self.conn.close()
