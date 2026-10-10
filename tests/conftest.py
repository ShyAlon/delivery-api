"""Tests must never reach a real database.

delivery_api.config calls load_dotenv() at import, so a TURSO_* or DELIVERY_*
setting in a .env file (or the shell) would point every test module at the real
Turso database or the real local database file. load_dotenv(override=False)
never replaces a variable that is already set, even to an empty string, so set
these before any test module imports the app. Tests that need a replica pass
their own fake connection.
"""
import os
import tempfile

_scratch = tempfile.mkdtemp(prefix="delivery-api-tests-")
for _name in ("TURSO_DATABASE_URL", "TURSO_AUTH_TOKEN"):
    os.environ[_name] = ""
os.environ["TURSO_SYNC_INTERVAL"] = "60"
os.environ["XDG_DATA_HOME"] = _scratch
os.environ["DELIVERY_DATABASE_URL"] = f"sqlite:///{_scratch}/delivery.db"
os.environ.pop("DELIVERY_DB_PATH", None)

# Private item6 scripts are test-only modules, not installed application imports.
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts/item6'))
