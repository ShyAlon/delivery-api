"""The operator health page: what it reads, who may read it, and what the log download leaves out."""
import json
import logging
import time
from types import SimpleNamespace as NS

import pytest

from delivery_api import ops_health, run_slots, server_log_buffer


@pytest.fixture(autouse=True)
def _clean():
    ops_health.reset();run_slots.reset()
    yield
    ops_health.reset();run_slots.reset()


def test_signed_in_users_are_counted_only_inside_the_window_and_anonymous_is_ignored():
    now=1000000.0
    ops_health.touch('u1','Ann','ann@example.com',now=now-60)
    ops_health.touch('u2','Bo',None,now=now-20*60)
    ops_health.touch('anonymous',now=now);ops_health.touch(None,now=now)
    assert ops_health.active_users(now=now)==1
    assert ops_health.active_users(minutes=30,now=now)==2


def test_the_map_is_bounded(monkeypatch):
    monkeypatch.setattr(ops_health,'_MAX_SEEN',50)
    for i in range(80):ops_health.touch('u%d'%i,now=1000+i)
    assert len(ops_health._seen)<=50
    assert 'u79' in ops_health._seen and 'u0' not in ops_health._seen   # the oldest go first


def test_sessions_show_the_email_and_the_run_by_phase_never_by_task_or_id():
    now=time.time()
    ops_health.touch('owner-1','Ann','ann@example.com',now=now-30)
    ops_health.touch('owner-2','Display Only',None,now=now-90)
    run_slots.running['task-secret-id']={'started':now,'peak_rss_mb':1.0,'timed_out':False,'owner_id':'owner-1','phase':'needs'}
    rows=ops_health.sessions(now=now)
    assert [r['user'] for r in rows]==['ann@example.com','Display Only']
    assert rows[0]['running']=='Run 1 (Needs)' and rows[1]['running']=='None'
    assert 'task-secret-id' not in json.dumps(rows) and 'owner-1' not in json.dumps(rows)


def test_health_has_the_numbers_the_page_draws_and_no_money_or_vendor_words():
    ops_health.touch('u','Ann','ann@example.com')
    h=ops_health.health()
    for key in ('as_of','started','uptime_seconds','capacity','memory','cpu','sessions','running','recent_runs','flags','signed_in_users','restart_note'):
        assert key in h
    assert h['signed_in_users']==1 and h['uptime_seconds']>=0 and h['flags']
    text=json.dumps(h).lower()
    for word in ('expense','vendor','price','cost','usd','dollar','provider'):assert word not in text,word


def test_flags_say_what_is_wrong_in_plain_words():
    h={'memory':{'used_percent':91},'capacity':{'waiting':2},'cpu':{'container_percent':100,'cpus':1},'metrics_problem':'disk full','running':[{'state':'Past Time Limit'}]}
    text=' | '.join(f['text'] for f in ops_health._flags(h))
    assert '91%' in text and '2 runs waiting' in text and 'above 90%' in text and 'disk full' in text and 'time limit' in text
    assert ops_health._flags({'memory':{'used_percent':10},'capacity':{'waiting':0},'cpu':{'container_percent':5,'cpus':2},'metrics_problem':None,'running':[]})[0]['level']=='ok'


def test_the_download_removes_secrets_cuts_library_messages_and_drops_source_lines_and_explains_itself():
    server_log_buffer.uninstall();server_log_buffer._lines.clear();server_log_buffer.install()
    try:
        logging.getLogger('delivery_api.activity').info('Founder answer submitted for ann@example.com with token abcdEFGH1234abcdEFGH1234abcdEFGH1234abcdEFGH')
        try:
            raise ValueError('the user typed: '+'my secret business plan '*20)
        except ValueError:
            logging.getLogger('some.library').exception('request failed')
        name,body=ops_health.log_download(60)
    finally:
        server_log_buffer.uninstall();server_log_buffer._lines.clear()
    assert name.startswith('requi-server-log-') and name.endswith('.txt')
    assert body.startswith('Requi.re server log') and 'A restart empties it' in body and 'Exception text is cut' in body
    assert 'ann@example.com' not in body and 'abcdEFGH1234' not in body and '[email]' in body
    assert 'raise ValueError' not in body                       # traceback source line dropped
    assert body.count('my secret business plan')<=6 and '[cut]' in body   # message cut at 160 characters, not the full 20 repeats


def _case_a_non_admin_is_refused_and_an_admin_gets_the_page_data_and_a_recorded_download(monkeypatch):
    import delivery_api.app as module
    from delivery_api import auth, config
    from fastapi.testclient import TestClient
    monkeypatch.setattr(config,'CLOUD_TENANTS',False)
    monkeypatch.setattr(auth,'enabled',lambda:True)
    client=TestClient(module.app)
    for path in ('/api/admin/operations/health','/api/admin/operations/log-download'):
        assert client.get(path).status_code in (401,403)
    module.app.dependency_overrides[auth.require_admin]=lambda:auth.Actor('admin','Admin','shy@example.com',True)
    try:
        health=client.get('/api/admin/operations/health')
        assert health.status_code==200 and 'capacity' in health.json()
        download=client.get('/api/admin/operations/log-download?minutes=15')
        assert download.status_code==200 and download.headers['content-type']=='application/zip'
        assert 'attachment; filename="requi-server-log-' in download.headers['content-disposition'] and download.headers['content-disposition'].endswith('.zip"') and download.headers['cache-control']=='no-store'
        assert client.get('/api/admin/operations/log-download?minutes=999').status_code==422
    finally:
        module.app.dependency_overrides.pop(auth.require_admin,None)


def _case_the_page_and_its_script_are_served_without_data():
    import delivery_api.app as module
    from fastapi.testclient import TestClient
    client=TestClient(module.app)
    page=client.get('/ops');script=client.get('/assets/ops.js');body=client.get('/assets/ops-body.html')
    assert page.status_code==200 and script.status_code==200 and 'fetch(' in script.text
    assert body.status_code==200 and 'Server Health' in body.text   # the view markup the shell mounts at /ops
    assert 'example.com' not in page.text   # a static shell: no user data


def _check_a_non_admin_is_refused_and_an_admin_gets_the_page_data_and_a_recorded_download(monkeypatch,tmp_path,workspace):
    from tests.test_ops_health import _case_a_non_admin_is_refused_and_an_admin_gets_the_page_data_and_a_recorded_download
    _case_a_non_admin_is_refused_and_an_admin_gets_the_page_data_and_a_recorded_download(monkeypatch)


def test_a_non_admin_is_refused_and_an_admin_gets_the_page_data_and_a_recorded_download(pg_local_compat_process):
    pg_local_compat_process(_check_a_non_admin_is_refused_and_an_admin_gets_the_page_data_and_a_recorded_download)


def _check_the_page_and_its_script_are_served_without_data(monkeypatch,tmp_path,workspace):
    from tests.test_ops_health import _case_the_page_and_its_script_are_served_without_data
    _case_the_page_and_its_script_are_served_without_data()


def test_the_page_and_its_script_are_served_without_data(pg_local_compat_process):
    pg_local_compat_process(_check_the_page_and_its_script_are_served_without_data)
