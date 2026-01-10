from contextlib import contextmanager

import crader.storage.connector as connector_module


class FakeConn:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


class FakePool:
    def __init__(self, conninfo, min_size, max_size, kwargs, configure):
        self.conninfo = conninfo
        self.configure = configure
        self._conn = FakeConn()
        configure(self._conn)

    def wait(self):
        self.ready = True

    @contextmanager
    def connection(self):
        yield self._conn

    def close(self):
        self._conn.close()


def test_pooled_connector_get_connection(monkeypatch):
    monkeypatch.setattr(connector_module, "ConnectionPool", FakePool)
    monkeypatch.setattr(connector_module, "register_vector", lambda _conn: None)

    connector = connector_module.PooledConnector("dsn://", min_size=1, max_size=2)
    with connector.get_connection() as conn:
        assert isinstance(conn, FakeConn)
    connector.close()
    assert conn.closed is True


def test_single_connector_reconnect(monkeypatch):
    conn = FakeConn()

    def fake_connect(*_args, **_kwargs):
        return conn

    monkeypatch.setattr(connector_module.psycopg, "connect", fake_connect)
    monkeypatch.setattr(connector_module, "register_vector", lambda _conn: None)
    connector = connector_module.SingleConnector("dsn://")

    conn.closed = True
    with connector.get_connection() as returned:
        assert returned is conn

    connector.close()
    assert conn.closed is True
