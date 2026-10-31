"""No retired storage implementation or install input can survive the PG cutover."""
from pathlib import Path
ROOT=Path(__file__).parents[1]


def test_runtime_and_install_inputs_have_no_retired_database_driver():
    for path in ('src/delivery_api/app.py','scripts/build-dist.sh','Dockerfile.full','pyproject.toml'):
        text=(ROOT/path).read_text().lower()
        assert 'import sqlite3' not in text,path
        assert 'langgraph-checkpoint-sqlite' not in text,path
        assert 'libsql' not in text,path
    assert not (ROOT/'scripts/ops-backup.py').exists()
    assert not (ROOT/'scripts/ops/latency_compare.py').exists()


def test_packaging_smoke_owns_postgres_and_preserves_network_fence():
    workflow=(ROOT/'.github/workflows/ci-cd.yml').read_text()
    assert 'owned-pg-smoke.py' in workflow and 'Dockerfile.pg-smoke' in workflow
    assert '--network none' in workflow and '--read-only' in workflow
    supervisor=(ROOT/'scripts/docker/owned-pg-smoke.py').read_text()
    assert 'refuse inherited database configuration' in supervisor
    assert "STATE['status']!='running'" in supervisor
    for name in ('control-plane-smoke.py','full-smoke.py'):
        text=(ROOT/'scripts/docker'/name).read_text()
        assert 'sqlite' not in text and 'DELIVERY_TEST_POSTGRES_URL' in text


def test_lock_and_current_config_have_no_retired_driver():
    lock=(ROOT/'uv.lock').read_text()
    assert 'name = "libsql"' not in lock
    assert 'name = "langgraph-checkpoint-sqlite"' not in lock
    assert 'sqlite:' not in (ROOT/'Dockerfile').read_text()
