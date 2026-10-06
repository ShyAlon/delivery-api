import os


def test_probe_turso_is_off_and_database_is_scratch():
    from delivery_api import config
    assert config.TURSO_DATABASE_URL == "" and config.TURSO_AUTH_TOKEN == ""
    assert "delivery-api-tests-" in str(config.local_database_path())
    assert "delivery-api-tests-" in str(config.DEFAULT_DB_PATH)
