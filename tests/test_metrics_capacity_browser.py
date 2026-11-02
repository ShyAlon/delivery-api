"""Real operator registry + two PG host sources in the existing Debug dialog."""
from test_artifacts import evidence_path
from test_ui_playwright import pg_ui,pg_page,pg_ids
from test_debug_package_browser import _operator_cookie
from playwright.sync_api import expect


def test_debug_capacity_shows_distinct_pg_host_series(pg_page,pg_ui,pg_ids,pg_browser,monkeypatch):
    import psycopg,time
    from pg_harness import STATE
    from delivery_api.postgres_metrics import PostgresMetrics
    page=pg_page;workspace=pg_browser[2]
    with psycopg.connect(STATE['dsn']) as c:
        previous=c.execute('SELECT subject FROM operator_principals WHERE enabled').fetchall()
        c.execute('UPDATE operator_principals SET enabled=false WHERE enabled')
        c.execute("INSERT INTO operator_principals(subject,registry_version,capabilities,enabled) VALUES(%s,'ops-v1',ARRAY['logs.read'],true)",(workspace.scope.owner_id,))
    dsn=STATE['dsn'].replace('user=sandbox','user=delivery_app')
    for host,size in (('host-alpha',20),('host-beta',80)):
        repo=PostgresMetrics(dsn,source_id=host)
        repo.append_container(ts=time.time(),mem_used_mb=size,mem_limit_mb=100,cpu_seconds=1,cpu_pct=size)
        repo.append_run(ts=time.time(),run_key='browser-'+host,task_id=pg_ids['live'],session_id='session',phase='needs',pid=1,procs=1,rss_mb=size,peak_rss_mb=size,cpu_seconds=1,cpu_pct=size)
    from delivery_api import app as app_module
    from delivery_api.operator_reads import OperatorReadStore
    from delivery_api.operator_authorization import OperatorPrincipal
    monkeypatch.setattr(app_module.app.state,'operator_read_store',OperatorReadStore(dsn,
        OperatorPrincipal(workspace.scope.owner_id,'ops-v1',frozenset({'logs.read'}))),raising=False)
    try:
        actor=_operator_cookie(page,pg_ui,workspace)
        page.goto(pg_ui+'/tasks/'+pg_ids['live']+'/view')
        page.get_by_role('button',name='Account menu for '+actor).click()
        page.get_by_role('menuitem',name='Debug',exact=True).click()
        expect(page.locator('#caphost')).to_contain_text('host-alpha')
        expect(page.locator('#caphost')).to_contain_text('host-beta')
        expect(page.locator('#caphost')).to_contain_text('memory average 80')
        page.locator('#caphost').scroll_into_view_if_needed()
        page.screenshot(path=evidence_path('pg-metrics-capacity-desktop.png'),full_page=False)
        page.set_viewport_size({'width':390,'height':844})
        page.locator('#caphost').scroll_into_view_if_needed()
        page.screenshot(path=evidence_path('pg-metrics-capacity-mobile.png'),full_page=False)
    finally:
        with psycopg.connect(STATE['dsn']) as c:
            c.execute('DELETE FROM operator_principals WHERE subject=%s',(workspace.scope.owner_id,))
            for (subject,) in previous:c.execute('UPDATE operator_principals SET enabled=true WHERE subject=%s',(subject,))
