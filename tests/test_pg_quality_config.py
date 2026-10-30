"""Real PG global engine settings: restart visibility and restricted writes."""
import pytest,psycopg
from pg_harness import STATE


def test_two_hosts_share_quality_config_without_files_and_invalid_edits_are_atomic(tmp_path):
    from delivery_api.pg_quality_config import PostgresQualityConfig
    from interview_engine.question_config import ConfigError
    dsn=STATE['dsn'].replace('user=sandbox','user=delivery_app')
    one=PostgresQualityConfig(dsn);two=PostgresQualityConfig(dsn)
    assert one.get()['retrieval']['max_words']==90
    with psycopg.connect(STATE['dsn']) as c:
        c.execute("INSERT INTO engine_quality_config(config_key,body) VALUES('question-quality',%s::jsonb)",(__import__('json').dumps({'retrieval':{'max_words':120}}),))
    assert two.get()['retrieval']['max_words']==120
    assert PostgresQualityConfig(dsn).get()['retrieval']['max_words']==120
    with pytest.raises(ConfigError):one.put({'retrieval':{'max_words':9999}})
    with pytest.raises(PermissionError):one.put({'retrieval':{'max_words':130}})
    assert two.get()['retrieval']['max_words']==120 and not list(tmp_path.iterdir())
    with psycopg.connect(STATE['dsn']) as c:c.execute('DELETE FROM engine_quality_config')


def test_runtime_quality_role_has_no_tenant_payment_or_other_settings_access():
    from delivery_api.pg_quality_config import PostgresQualityConfig
    repo=PostgresQualityConfig(STATE['dsn'].replace('user=sandbox','user=delivery_app'))
    with repo.transaction() as c:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):c.execute('SELECT * FROM credit_accounts')


def test_pg_configuration_corruption_is_not_silently_defaulted():
    from delivery_api.pg_quality_config import PostgresQualityConfig
    from interview_engine.question_config import ConfigError
    with psycopg.connect(STATE['dsn']) as c:
        c.execute("INSERT INTO engine_quality_config(config_key,body) VALUES('question-quality','{\"retrieval\":{\"max_words\":9999}}')")
    try:
        with pytest.raises(ConfigError):PostgresQualityConfig(STATE['dsn'].replace('user=sandbox','user=delivery_app')).get()
    finally:
        with psycopg.connect(STATE['dsn']) as c:c.execute('DELETE FROM engine_quality_config')


def test_pg_quality_app_routes_and_background_thread_read_latest_database_config():
    from delivery_api.pg_quality_config import PostgresQualityConfig
    from interview_engine.http_service import create_app,SessionRegistry
    from interview_engine import question_config as q
    from fastapi.testclient import TestClient
    import asyncio
    backend=PostgresQualityConfig(STATE['dsn'].replace('user=sandbox','user=delivery_app'))
    app=create_app(SessionRegistry(),service_token='fixture',quality_store=backend)
    @app.get('/probe')
    async def probe():return await asyncio.to_thread(lambda:q.current()['retrieval'])
    with TestClient(app) as client:
        assert client.get('/probe').json()['max_words']==90
        with psycopg.connect(STATE['dsn']) as c:
            c.execute("INSERT INTO engine_quality_config(config_key,body) VALUES('question-quality','{\"retrieval\":{\"max_words\":125}}')")
        try:
            assert client.get('/probe').json()['max_words']==125
            assert client.get('/v1/question-quality-config',headers={'x-interview-engine-token':'fixture'}).json()['config']['retrieval']['max_words']==125
            assert client.get('/v1/question-quality-config').status_code==401
        finally:
            with psycopg.connect(STATE['dsn']) as c:c.execute('DELETE FROM engine_quality_config')


def test_quality_preflight_checks_real_pg_role_and_rejects_legacy_file_override():
    import importlib.util
    from pathlib import Path
    spec=importlib.util.spec_from_file_location('quality_preflight',Path(__file__).parents[1]/'scripts/docker/preflight.py')
    preflight=importlib.util.module_from_spec(spec);__import__('sys').modules[spec.name]=preflight;spec.loader.exec_module(preflight)
    dsn=STATE['dsn'].replace('user=sandbox','user=delivery_app')
    env={'DELIVERY_DATABASE_URL':dsn}
    assert preflight._check_engine_quality(env,psycopg.connect).status=='pass'
    with psycopg.connect(STATE['dsn']) as c:
        c.execute("INSERT INTO engine_quality_config(config_key,body) VALUES('question-quality','{\"retrieval\":{\"max_words\":9999}}')")
    try:assert preflight._check_engine_quality(env,psycopg.connect).status=='fail'
    finally:
        with psycopg.connect(STATE['dsn']) as c:c.execute('DELETE FROM engine_quality_config')
    env['INTERVIEW_ENGINE_QUESTION_QUALITY_CONFIG']='/tmp/forbidden.json'
    assert preflight._check_engine_quality(env,psycopg.connect).status=='fail'


def test_standalone_tenant_pg_config_uses_one_router_pool_and_no_file_override(monkeypatch,tmp_path):
    from interview_engine import http_service as service,pg_pool
    from delivery_api.db_pool import pool_for
    from interview_engine.question_config import bound_store,current
    dsn=STATE['dsn'].replace('user=sandbox','user=delivery_app')
    pool=pg_pool.build_pool(dsn);calls=[]
    monkeypatch.setenv('INTERVIEW_ENGINE_TENANTS','1')
    monkeypatch.setenv('INTERVIEW_ENGINE_TOKEN','fixture-private')
    monkeypatch.setenv(service.SESSIONS_DSN_ENV,dsn)
    monkeypatch.setenv('VMODEL_ARTIFACTS_DIR',str(tmp_path))
    monkeypatch.setattr(pg_pool,'build_pool',lambda value:(calls.append(value) or pool))
    app=service._build_service_app();router=app.state.sessions
    assert router.quality_config_pool() is pool
    assert router.quality_config_pool() is pool and calls==[dsn]
    from fastapi.testclient import TestClient
    from test_postgres_store import A
    with TestClient(app) as client:
        got=client.get('/v1/question-quality-config',headers={'x-interview-engine-token':'fixture-private',
            'x-requi-owner':A.owner_id,'x-requi-session-id':A.session_id})
        assert got.status_code==200 and got.json()['load_status']['source']=='postgres'
    assert list(tmp_path.iterdir())==[]
    monkeypatch.setenv('INTERVIEW_ENGINE_QUESTION_QUALITY_CONFIG',str(tmp_path/'forbidden.json'))
    with pytest.raises(ValueError,match='legacy quality file'):service._build_service_app()


def test_runtime_config_role_denies_sql_writes_and_public_roles_cannot_read():
    dsn=STATE['dsn'].replace('user=sandbox','user=delivery_app')
    with psycopg.connect(dsn) as c:
        c.execute('SET LOCAL ROLE requi_engine_config_read')
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            c.execute("INSERT INTO engine_quality_config VALUES('question-quality','{}',now())")
    with psycopg.connect(STATE['dsn']) as c:
        for role in ('anon','authenticated','service_role','requi_backend','requi_operator_read'):
            assert c.execute("SELECT has_table_privilege(%s,'public.engine_quality_config','SELECT')",(role,)).fetchone()[0] is False


def test_host_real_worker_binding_reads_pg_not_ambient_config_and_resets(tmp_path,monkeypatch):
    import threading
    from types import SimpleNamespace
    from delivery_api.inprocess_interview import InProcessInterviewHost
    from delivery_api.pg_quality_config import PostgresQualityConfig
    from interview_engine import question_config as q
    from spec_generator import cooperative_run
    backend=PostgresQualityConfig(STATE['dsn'].replace('user=sandbox','user=delivery_app'))
    host=InProcessInterviewHost(None,tmp_path,gateway_factory=lambda *a:None,generator_clients=None,interview_clients=None)
    host._quality_store=backend
    ambient=q.ConfigStore();ambient.put({'retrieval':{'max_words':170}})
    with psycopg.connect(STATE['dsn']) as c:
        c.execute("INSERT INTO engine_quality_config(config_key,body) VALUES('question-quality','{\"retrieval\":{\"max_words\":130}}')")
    results=[];errors=[]
    def worker():
        token=cooperative_run._cli_fence.set((SimpleNamespace(check=lambda *a:None),1))
        try:
            with q.bound_store(ambient):
                with host._bound(SimpleNamespace(),'fixture-job'):
                    results.append(q.current()['retrieval']['max_words'])
                results.append(q.current()['retrieval']['max_words'])
        except Exception as error:errors.append(error)
        finally:cooperative_run._cli_fence.reset(token)
    try:
        thread=threading.Thread(target=worker);thread.start();thread.join(timeout=5)
        assert not thread.is_alive() and errors==[] and results==[130,170]
    finally:
        with psycopg.connect(STATE['dsn']) as c:c.execute('DELETE FROM engine_quality_config')
