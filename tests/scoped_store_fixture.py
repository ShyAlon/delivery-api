"""Keep store construction real; double only the inspected SQL connection."""
from contextlib import contextmanager
from delivery_api.postgres_store import PostgresStore


def scoped_store(monkeypatch, connection):
    transactions = []
    class Postgres:
        @contextmanager
        def transaction(self, **kwargs):
            transactions.append(kwargs)
            yield connection
    monkeypatch.setattr('delivery_api.postgres_store.ScopedPostgres', lambda dsn: Postgres())
    return PostgresStore('fixture-only'), transactions
