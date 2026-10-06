import os


def test_probe_turso_env_is_cleared():
    assert not [k for k in os.environ if k.startswith("TURSO")]
