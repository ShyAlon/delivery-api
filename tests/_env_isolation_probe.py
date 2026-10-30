

def test_probe_turso_is_off_and_database_is_scratch():
    from delivery_api import config
    assert not hasattr(config,"TURSO_DATABASE_URL") and not hasattr(config,"TURSO_AUTH_TOKEN")
    assert not hasattr(config,"local_database_path") and not hasattr(config,"DEFAULT_DB_PATH")
    assert "delivery-api-tests-" in str(config.default_data_dir())
    assert not (config.default_data_dir()/"delivery.db").exists()

    # The active profile must point only at the newly owned disposable DB.
    assert config.CLOUD_TENANTS is True
    if config.CLOUD_TENANTS:
        import pg_harness
        import psycopg
        from psycopg.conninfo import conninfo_to_dict
        from urllib.parse import urlparse, parse_qs, unquote
        assert pg_harness.STATE['status'] == 'running'
        owned = conninfo_to_dict(pg_harness.STATE['dsn'])
        selected = urlparse(config.DATABASE_URL)
        query = parse_qs(selected.query)
        assert query['host'] == [owned['host']]
        assert query['user'] == ['delivery_app']
        assert unquote(selected.path.lstrip('/')) == owned['dbname']
        assert owned['host'].startswith('/tmp/delivery-api-pg-')
        assert owned['dbname'].startswith('test_tenant_')
        with psycopg.connect(config.DATABASE_URL) as connection:
            assert connection.execute('SELECT current_user,current_database()').fetchone() == ('delivery_app',owned['dbname'])
