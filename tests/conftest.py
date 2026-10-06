"""Tests must never reach a real database.

delivery_api.config reads TURSO_DATABASE_URL / TURSO_AUTH_TOKEN at import and
delivery_api.app builds its store from them, so a shell that exports them would
point every test module at the real Turso database. Clear them before any test
module is imported. Tests that need a replica pass their own fake connection.
"""
import os

for _name in ("TURSO_DATABASE_URL", "TURSO_AUTH_TOKEN", "TURSO_SYNC_INTERVAL"):
    os.environ.pop(_name, None)
