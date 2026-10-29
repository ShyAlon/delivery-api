"""Server log buffer: scrubbing, per-account visibility, bounds. Browser scrubber: run under node when it is installed."""
import json
import logging
import shutil
import subprocess
from pathlib import Path

import pytest

from delivery_api import server_log_buffer as slb
from delivery_api.tenancy import TenantScope, tenant_context

A='11111111-1111-4111-8111-111111111111'
B='22222222-2222-4222-8222-222222222222'
WEB=Path(__file__).resolve().parents[1]/'src'/'delivery_api'/'web'


@pytest.fixture(autouse=True)
def clean():
    slb.uninstall();slb._lines.clear();slb.install()
    yield
    slb.uninstall();slb._lines.clear()


def emit(owner,text,level=logging.WARNING,name='delivery_api.test'):
    log=logging.getLogger(name)
    if owner is None:log.log(level,text);return
    with tenant_context(TenantScope(owner)):log.log(level,text)


def read(owner=A,admin=False,minutes=30):
    return slb.read(owner,admin,minutes)


def test_customer_sees_only_own_lines_and_never_unowned():
    emit(A,'mine');emit(B,'theirs');emit(None,'system line')
    out=read()['text']
    assert 'mine' in out and 'theirs' not in out and 'system line' not in out


def test_operator_sees_everything_labelled():
    emit(A,'mine');emit(B,'theirs');emit(None,'system line')
    r=read(admin=True);out=r['text']
    assert 'mine' in out and 'theirs' in out and 'system line' in out and '[system]' in out and '[you]' in out
    assert r['scope'].startswith('all accounts')


@pytest.mark.parametrize('secret',[
    'user shy@example.com failed','Authorization: Bearer abc.def.ghi-123','password=hunter2hunter2',
    'eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dBjftJeZ4CVPmB92K27uhbUJU1p1r','key sk-live-abcdefghijklmnop1234',
    'postgresql://svc:s3cr3t@db.internal:5432/app','hash '+'a1b2c3d4'*8,'blob '+'Ab3'*20,'GET /x?access_token=zzz999&ok=1'])
def test_secrets_are_scrubbed(secret):
    emit(A,secret)
    out=read()['text']
    for bad in ('shy@example.com','abc.def.ghi-123','hunter2','dBjftJeZ4CVPmB92K27uhbUJU1p1r','abcdefghijklmnop1234','s3cr3t','a1b2c3d4'*4,'Ab3'*14,'zzz999'):
        assert bad not in out


def test_scrubbing_also_applies_on_read():
    slb._lines.append((__import__('time').time(),A,'WARNING','x','leak shy@example.com'))
    assert 'shy@example.com' not in read()['text']


def test_ordinary_text_survives():
    emit(A,'engine.start failed for task t-123 at /api/tasks/t-123/start')
    assert 'engine.start failed for task t-123 at /api/tasks/t-123/start' in read()['text']


def test_buffer_is_bounded_and_window_is_applied():
    for i in range(slb.MAX_LINES+50):emit(A,'n%d'%i)
    r=read()
    assert r['count']<=slb.MAX_LINES and r['buffer_full'] and 'n%d'%(slb.MAX_LINES+49) in r['text']
    old=__import__('time').time()-3600
    slb._lines.appendleft((old,A,'WARNING','x','ancient'))
    assert 'ancient' not in read(minutes=30)['text']


def test_range_is_reported_and_clamped():
    r=read(minutes=99999)
    assert r['minutes']==240 and r['since'] and r['until'] and r['scrubbed'] is True


def test_info_below_threshold_is_not_collected_from_root():
    emit(A,'chatty',level=logging.INFO,name='some.library')
    assert 'chatty' not in read()['text']


def test_install_is_idempotent():
    slb.install();slb.install()
    emit(A,'once')
    assert read()['text'].count('once')==1


def run_node(code):
    node=shutil.which('node')
    if not node:pytest.skip('node is not installed')
    tmp=Path(__file__).parent/'_scrub_probe.mjs'
    tmp.write_text((WEB/'log-scrub.js').read_text()+'\n'+code)
    try:
        return subprocess.run([node,str(tmp)],capture_output=True,text=True,timeout=30,check=True).stdout
    finally:tmp.unlink()


def test_browser_scrubber_removes_secrets_and_keeps_paths():
    out=run_node("""console.log(JSON.stringify([scrubText('Bearer abc.def GET /api/tasks/t1/start?token=zzz9 me shy@example.com'),scrubText('{"password":"hunter2"}'),scrubText('ok /api/tasks/t1')]))""")
    a,b,c=json.loads(out)
    assert 'abc.def' not in a and 'zzz9' not in a and 'shy@example.com' not in a and '/api/tasks/t1/start' in a
    assert 'hunter2' not in b and c=='ok /api/tasks/t1'


def test_client_report_states_range_and_has_no_bodies():
    out=run_node("""const r=buildClientReport({requests:[{ts:'t',status:500,method:'POST',url:'/api/tasks/t1/start?x=1',total_ms:5,server_ms:null,ref:'r1'}],records:[{ts:'t',kind:'error',path:'/',message:'boom shy@example.com'}],startedAt:'S',url:'https://h/tasks/t1?code=9',build:'abc',now:'N'});console.log(JSON.stringify(r))""")
    r=json.loads(out)
    assert 'this tab since S' in r['range'] and '1 requests' in r['range']
    assert 'shy@example.com' not in r['text'] and 'x=1' not in r['text'] and 'code=9' not in r['text'] and 'ref r1' in r['text'] and '--- browser events ---' in r['text']


def test_debug_dialog_shows_both_logs_in_black_boxes_and_offers_summary_and_everything_zip():
    s=(WEB/'identity.js').read_text()
    for needle in ('id="copysummary"','id="downloadeverything"','id="serverloghost"','id="clientloghost"','serverReport','clientReport','Preparing','Copied','Failed'):
        assert needle in s


def test_app_exposes_the_log_route_and_installs_the_buffer():
    s=(Path(__file__).resolve().parents[1]/'src'/'delivery_api'/'app.py').read_text()
    assert '@app.get("/api/server-log")' in s and 'server_log_buffer.install()' in s
    assert 'Depends(auth.require_actor)' in s.split('@app.get("/api/server-log")')[1].split('\n\n')[0]


def test_export_part_scrubber_keeps_json_valid_and_preserves_session_ids():
    out=run_node("""console.log(JSON.stringify([scrubPart('{"session_id":"eab542ae-4636-48c8-868a-43292118b9ef","nested":{"access_token":"abc123xyz"},"url":"https://h/x?token=leakme","email":"shy@example.com"}'),scrubPart('plain text with Bearer abc.def.ghi')]))""")
    redacted,plain=json.loads(out)
    parsed=json.loads(redacted)
    assert parsed['session_id']=='eab542ae-4636-48c8-868a-43292118b9ef'   # diagnostic, not a credential
    assert parsed['nested']['access_token']=='[redacted]'
    assert 'leakme' not in redacted and 'shy@example.com' not in redacted
    assert 'abc.def.ghi' not in plain


def test_export_part_scrubber_redacts_nested_urls_and_errors():
    out=run_node("""console.log(scrubPart(JSON.stringify({error:{detail:"connect postgresql://postgres:hunter2secret@db.example.com/x failed",chain:[{authorization:"Bearer abc.def.ghi"}]}})))""")
    assert 'hunter2secret' not in out and 'abc.def.ghi' not in out
    parsed=json.loads(out)   # stays valid JSON
    assert parsed['error']['chain'][0]['authorization']=='[redacted]'


def test_export_scrubs_owner_qualified_task_id_in_urls_and_log_text():
    out=run_node("""const id='12345678-1234-4123-a123-123456789abc-live';console.log(JSON.stringify([scrubPart(JSON.stringify({task_id:id,url:'https://h/tasks/'+id+'/view',nested:{request:'/api/tasks/'+id+'/events'}})),scrubPart('GET /api/tasks/'+id+'/events task='+id),scrubText('/tasks/12345678-1234-4123-a123-123456789abc/view')]))""")
    structured,log,bare_path=json.loads(out)
    assert '12345678-1234-4123-a123-123456789abc' not in structured+log+bare_path
    parsed=json.loads(structured)
    assert parsed['task_id']=='[token]'
    assert parsed['url']=='https://h/tasks/[token]/view'
    assert parsed['nested']['request']=='/api/tasks/[token]/events'
    assert log=='GET /api/tasks/[token]/events task=[token]'
    assert bare_path=='/tasks/[token]/view'
