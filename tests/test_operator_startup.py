import asyncio,json
from types import SimpleNamespace
from delivery_api import app as m
from test_postgres_store import A


def test_real_lifespan_installs_explicit_operator_reader(monkeypatch):
    from delivery_api import cloud_execution,cloud_runtime,run_metrics
    monkeypatch.setattr(m.config,'CLOUD_TENANTS',True)
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
    # No launcher-side filtered copy or new capability defaults: values pass
    # unchanged, and production operator validation remains the authority.
    import sys
    from pathlib import Path
    sys.path.insert(0,str(Path(__file__).parents[1]/'scripts/docker'))
    from image_settings import validate
    source=tmp_path/'private.env'
    raw='DELIVERY_DATABASE_URL=postgresql://fixture/db\nDELIVERY_SUPABASE_URL=https://fixture.supabase.co\nDELIVERY_SUPABASE_ANON_KEY=public\nINTERVIEW_ENGINE_TOKEN=fictional-private-token-123456\nREQUI_OPERATOR_SUBJECT=subject\nREQUI_OPERATOR_REGISTRY_VERSION=v1\nREQUI_OPERATOR_CAPABILITIES=["logs.read"]\nDEEPSEEK_API_KEY=fictional-never-copy\n'
    source.write_text(raw);source.chmod(0o600)
    settings=validate(source)
    assert settings['REQUI_OPERATOR_SUBJECT']=='subject'
    assert settings['REQUI_OPERATOR_CAPABILITIES']=='["logs.read"]'
    assert source.read_text()==raw and list(tmp_path.iterdir())==[source]



def test_admin_badge_cannot_mint_an_operator_principal(monkeypatch,store):
    import pytest
    from fastapi import HTTPException
    from delivery_api import auth
    monkeypatch.setattr(m.config,'CLOUD_TENANTS',True)
    monkeypatch.setattr(m,'store',store)
    monkeypatch.setattr(m.app.state,'operator_read_store',None,raising=False)
    from delivery_api import operator_reads
    built=[];original=operator_reads.OperatorReadStore
    def record(*args,**kwargs):built.append(args);return original(*args,**kwargs)
    monkeypatch.setattr(operator_reads,'OperatorReadStore',record)
    actor=auth.Actor(A.owner_id,'Admin',None,True)
    for call in (lambda:m.admin_operations_stops(actor),lambda:m.admin_operations_attempt_releases(50,actor),lambda:m.admin_operations_spend(actor),lambda:m.admin_operations_grants(actor)):
        with pytest.raises(HTTPException) as denied:call()
        assert denied.value.status_code==503
    assert built==[],"an admin badge must never mint a route-specific principal"
