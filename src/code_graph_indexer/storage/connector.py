import logging
import contextlib
from typing import Protocol, Generator, Any
import psycopg
from psycopg.rows import dict_row
from pgvector.psycopg import register_vector
from psycopg_pool import ConnectionPool

logger = logging.getLogger(__name__)

class DatabaseConnector(Protocol):
    """
    Interfaccia Contract per qualsiasi fornitore di connessioni Postgres.
    Garantisce che lo Storage riceva sempre una connessione valida,
    indipendentemente da come questa venga creata (Pool o Single).
    """
    def get_connection(self) -> Generator[psycopg.Connection, None, None]:
        ...
    
    def close(self):
        ...

class PooledConnector:
    """
    Gestisce un pool di connessioni Client-Side.
    
    BEST PRACTICE:
    Usare questo connettore nel Main Process (API, Celery Orchestrator) dove
    ci sono thread concorrenti che fanno query brevi e frequenti.
    """
    def __init__(self, dsn: str, min_size: int = 4, max_size: int = 20):
        self._dsn = dsn
        self.pool = ConnectionPool(
            conninfo=dsn,
            min_size=min_size,
            max_size=max_size,
            kwargs={
                "row_factory": dict_row,
                "autocommit": True 
            },
            configure=self._configure
        )
        # Attendiamo che il pool sia pronto
        self.pool.wait()

    def _configure(self, conn: psycopg.Connection):
        """Configura ogni nuova connessione del pool (es. pgvector)."""
        try:
            register_vector(conn)
        except psycopg.ProgrammingError:
            pass 

    @contextlib.contextmanager
    def get_connection(self):
        with self.pool.connection() as conn:
            yield conn

    def close(self):
        self.pool.close()

class SingleConnector:
    """
    Gestisce una singola connessione persistente dedicata.
    
    BEST PRACTICE:
    Usare questo connettore nei Worker Process (Multiprocessing) che vivono
    solo per eseguire Batch Operations (COPY) e non hanno concorrenza interna.
    """
    def __init__(self, dsn: str):
        self.dsn = dsn
        self.conn: Any = None
        self._connect()

    def _connect(self):
        """Apre una connessione diretta (TCP socket) verso il DB/PgBouncer."""
        self.conn = psycopg.connect(
            self.dsn, 
            autocommit=True, 
            row_factory=dict_row
        )
        try:
            register_vector(self.conn)
        except psycopg.ProgrammingError:
            pass

    @contextlib.contextmanager
    def get_connection(self):
        # Resilienza: Se la connessione è caduta (es. timeout PgBouncer), riconnettiamo.
        if self.conn.closed:
            logger.warning("⚠️ SingleConnector: Connection lost. Reconnecting...")
            self._connect()
        
        yield self.conn

    def close(self):
        if self.conn and not self.conn.closed:
            self.conn.close()