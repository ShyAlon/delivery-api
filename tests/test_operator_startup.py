import asyncio,json
from types import SimpleNamespace
from delivery_api import app as m
from test_postgres_store import A


def test_real_lifespan_installs_explicit_operator_reader(monkeypatch):
    from delivery_api import cloud_execution,cloud_runtime,run_metrics
    monkeypatch.setattr(m.config,'CLOUD_TENANTS',True)
    monkeypatch.setattr(m.config,'validate_auth_config',lambda:None)
    monkeypatch.setattr(cloud_execution,'admitted',lambda:False)
    monkeypatch.setattr(cloud_runtime,'uninstall',lambda:None)
    monkeypatch.setattr(run_metrics,'start_idle_sampler',lambda:None)
    monkeypatch.setattr(run_metrics,'stop_idle_sampler',lambda:None)
    async def noop():pass
    monkeypatch.setattr(m.runner,'shutdown',noop);monkeypatch.setattr(m.jobs,'stop',noop)
    monkeypatch.setenv('REQUI_OPERATOR_SUBJECT',A.owner_id)
    monkeypatch.setenv('REQUI_OPERATOR_REGISTRY_VERSION','test-startup')
    monkeypatch.setenv('REQUI_OPERATOR_CAPABILITIES',json.dumps(['logs.read','debug.read']))
    async def run():
        async with m.lifespan(m.app):
            reader=m._operator_reader()
            assert reader.principal.subject==A.owner_id
            assert reader.principal.registry_version=='test-startup'
            assert reader.principal.capabilities==frozenset({'logs.read','debug.read'})
    prior=getattr(m.app.state,'operator_read_store',None)
    try:asyncio.run(run())
    finally:m.app.state.operator_read_store=prior


def test_missing_invalid_and_local_config_clear_stale_authority():
    from delivery_api.operator_startup import install
    from fastapi import FastAPI
    app=FastAPI()
    good={'REQUI_OPERATOR_SUBJECT':A.owner_id,'REQUI_OPERATOR_REGISTRY_VERSION':'test',
          'REQUI_OPERATOR_CAPABILITIES':'["logs.read"]'}
    for env in ({},{**good,'REQUI_OPERATOR_SUBJECT':'invalid'},
                {**good,'REQUI_OPERATOR_CAPABILITIES':'["logs.read","logs.read"]'},
                {**good,'REQUI_OPERATOR_CAPABILITIES':'["unreviewed"]'}):
        app.state.operator_read_store=object()
        install(app,env,dsn=m.config.DATABASE_URL,enabled=True)
        assert app.state.operator_read_store is None and 'disabled' in app.state.operator_read_status
    install(app,good,dsn=m.config.DATABASE_URL,enabled=False)
    assert app.state.operator_read_store is None


def test_startup_wiring_keeps_database_registry_and_active_session_checks(monkeypatch,store):
    import psycopg,pytest
    from fastapi import FastAPI
    from delivery_api.operator_startup import install
    from delivery_api.tenancy import tenant_context,TenantAccessDenied
    from test_postgres_store import B,DSN
    env={'REQUI_OPERATOR_SUBJECT':A.owner_id,'REQUI_OPERATOR_REGISTRY_VERSION':'startup-registry',
         'REQUI_OPERATOR_CAPABILITIES':'["logs.read"]'}
    app=FastAPI();install(app,env,dsn=DSN.replace('user=sandbox','user=delivery_app'),enabled=True)
    reader=app.state.operator_read_store
    with psycopg.connect(DSN) as c:
        c.execute('DELETE FROM operator_principals')
        c.execute('INSERT INTO operator_principals VALUES(%s,%s,%s,true)',(A.owner_id,'startup-registry',['logs.read']))
    try:
        with tenant_context(A):
            with reader._read(store,'logs.read'):pass
            from delivery_api import process_logs
            from types import SimpleNamespace
            monkeypatch.setattr(m,'store',store)
            monkeypatch.setattr(m.app.state,'operator_read_store',reader,raising=False)
            monkeypatch.setattr(m.config,'CLOUD_TENANTS',True)
            result=m.server_log(30,SimpleNamespace(id=A.owner_id,admin=True),None)
            assert result['scope']=='reviewed_server_file_operator_read'
            assert 'ephemeral' in result['storage'].lower()
            with pytest.raises(TenantAccessDenied):
                with reader._read(store,'debug.read'):pass
        with monkeypatch.context() as scoped:
            scoped.setattr(store,'auth_session_active',lambda:False)
            with tenant_context(A):
                with pytest.raises(TenantAccessDenied):
                    with reader._read(store,'logs.read'):pass
        with tenant_context(B):
            with pytest.raises(TenantAccessDenied):
                with reader._read(store,'logs.read'):pass
        with psycopg.connect(DSN) as c:c.execute('UPDATE operator_principals SET enabled=false')
        with tenant_context(A):
            with pytest.raises(TenantAccessDenied):
                with reader._read(store,'logs.read'):pass
    finally:
        with psycopg.connect(DSN) as c:c.execute('DELETE FROM operator_principals')


def test_actual_preview_filter_preserves_only_explicit_operator_settings(tmp_path):
    import subprocess
    from pathlib import Path
    script=(Path(__file__).parents[1]/'scripts/ops/preview-exec.sh').read_text()
    fn=script[script.index('build_exec_env(){'):script.index('\nfor t in docker curl git shasum tar')]
    source=tmp_path/'source';dest=tmp_path/'filtered'
    source.write_text('REQUI_OPERATOR_SUBJECT=subject\nREQUI_OPERATOR_REGISTRY_VERSION=v1\nREQUI_OPERATOR_CAPABILITIES=["logs.read"]\nREQUI_OPERATOR_EVIL=no\nDEEPSEEK_API_KEY=never-copy\n')
    result=subprocess.run(['bash','-c',fn+'\nbuild_exec_env "$1" "$2"','_',str(source),str(dest)],capture_output=True,text=True,check=True)
    body=dest.read_text()
    assert 'REQUI_OPERATOR_SUBJECT=subject' in body
    assert 'REQUI_OPERATOR_REGISTRY_VERSION=v1' in body
    assert 'REQUI_OPERATOR_CAPABILITIES=["logs.read"]' in body
    assert 'REQUI_OPERATOR_EVIL' not in body and 'never-copy' not in body
