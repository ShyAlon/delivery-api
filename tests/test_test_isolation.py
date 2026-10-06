import os
import subprocess
import sys
from pathlib import Path

from delivery_api.store import SQLiteStore


def test_conftest_clears_turso_env_before_tests_import_the_app():
    env = {**os.environ, "TURSO_DATABASE_URL": "libsql://real.example", "TURSO_AUTH_TOKEN": "secret"}
    probe = Path(__file__).with_name("_env_isolation_probe.py")
    done = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", str(probe)],
                          env=env, capture_output=True, text=True)
    assert done.returncode == 0, done.stdout[-500:]


def test_schema_migration_tolerates_columns_a_stale_replica_does_not_show(tmp_path):
    path = str(tmp_path / "stale.db")
    SQLiteStore(path)  # full schema, every migrated column present

    class StalePragma:
        """Reads like a stale local replica: table_info shows no columns, writes hit the full schema."""
        def __init__(self, inner): self._inner = inner
        def execute(self, sql, *args):
            if sql.startswith("PRAGMA table_info"):
                return iter(())
            return self._inner.execute(sql, *args)
        def __getattr__(self, name): return getattr(self._inner, name)

    import sqlite3
    inner = sqlite3.connect(path, check_same_thread=False)
    inner.row_factory = sqlite3.Row
    SQLiteStore(path, connection=StalePragma(inner))  # raised "duplicate column name: revision" before
