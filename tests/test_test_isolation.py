import os
import subprocess
import sys
from pathlib import Path



def test_conftest_turns_off_turso_and_real_database_before_tests_import_the_app():
    env = {**os.environ, "TURSO_DATABASE_URL": "libsql://real.example", "TURSO_AUTH_TOKEN": "secret", "DELIVERY_DATABASE_URL": "sqlite:////home/real/delivery.db"}
    # The child owns a fresh disposable server, not the outer fixture's DSN.
    env.pop('DELIVERY_TEST_POSTGRES_DSN', None)
    probe = Path(__file__).with_name("_env_isolation_probe.py")
    done = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", str(probe)],
                          env=env, capture_output=True, text=True, timeout=30)
    assert done.returncode == 0, done.stdout[-2000:]+done.stderr[-2000:]
