"""Theme 3: the engine process log reaches the Debug server log.

full_entry.py tees the engine's combined stdout/stderr into a bounded, stamped
file; delivery_api.engine_log reads a scrubbed tail back into /api/server-log with
the same audience rule as the in-memory buffer (own-account lines for customers,
system lines operator-only)."""
import importlib.util
import subprocess
import sys
import time
from datetime import datetime,timezone
from pathlib import Path

import delivery_api.engine_log as engine_log

ROOT=Path(__file__).resolve().parents[1]
_spec=importlib.util.spec_from_file_location('full_entry',ROOT/'scripts'/'docker'/'full_entry.py')
full_entry=importlib.util.module_from_spec(_spec);_spec.loader.exec_module(full_entry)

OWNER_A='owner-aaaa'
OWNER_B='owner-bbbb'
SESSION_A='11111111-1111-4111-8111-111111111111'
SESSION_B='22222222-2222-4222-8222-222222222222'


def _stamp(ts=None):
    return datetime.fromtimestamp(ts if ts is not None else time.time(),timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def _resolver(mapping):
    return lambda session:mapping.get(session)


def test_tee_copies_stdout_and_stamped_file(tmp_path):
    out_path=tmp_path/'logs'/'engine.log'
    proc=subprocess.Popen([sys.executable,'-c','import sys;print("hello engine");print("a warning",file=sys.stderr)'],
                          stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,bufsize=1)
    sink_lines=[]
    class Out:
        def write(self,line):sink_lines.append(line)
        def flush(self):pass
    full_entry.tee_engine_output(proc,out_path,out=Out())
    proc.wait()
    for _ in range(100):
        if len(sink_lines)>=2:break
        time.sleep(0.05)
    text=out_path.read_text()
    assert 'hello engine' in text and 'a warning' in text
    assert ''.join(sink_lines).count('hello engine')==1  # stdout view unchanged, no prefix
    for line in text.splitlines():
        assert engine_log._STAMP.match(line),line


def test_tee_rotates_at_bound(tmp_path):
    out_path=tmp_path/'engine.log'
    code='for i in range(400):print("x"*120)'
    proc=subprocess.Popen([sys.executable,'-c',code],stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,bufsize=1)
    full_entry.tee_engine_output(proc,out_path,max_bytes=4096,out=open('/dev/null','w'))
    proc.wait()
    for _ in range(100):
        if proc.stdout.closed:break
        time.sleep(0.05)
    assert out_path.with_suffix('.log.1').exists()
    assert out_path.stat().st_size<6000


def test_path_parity_with_delivery_config(tmp_path,monkeypatch):
    monkeypatch.setenv('XDG_DATA_HOME',str(tmp_path))
    assert full_entry.engine_log_path(dict(__import__('os').environ))==engine_log.default_path()


def test_read_windows_and_attributes_owners(tmp_path):
    path=tmp_path/'engine.log'
    old=time.time()-60*60
    lines=[
        _stamp(old)+' session.start.accept session=%s task=t1'%SESSION_A,          # outside window
        _stamp()+' session.published session=%s seq=1'%SESSION_A,
        _stamp()+' advance.return kind=question session=%s dur_ms=12'%SESSION_B,
        _stamp()+' uvicorn started without any session marker',
    ]
    path.write_text('\n'.join(lines)+'\n')
    resolver=_resolver({SESSION_A:OWNER_A,SESSION_B:OWNER_B})
    customer=engine_log.read(OWNER_A,False,30,resolver,path=path)
    assert SESSION_A in customer['text'] and SESSION_B not in customer['text']
    assert 'uvicorn started' not in customer['text']          # ownerless stays operator-only
    assert 'session.start.accept' not in customer['text']     # outside the window
    admin=engine_log.read(OWNER_A,True,30,resolver,path=path)
    assert SESSION_A in admin['text'] and SESSION_B in admin['text']
    assert 'uvicorn started' in admin['text'] and '[system]' in admin['text']
    assert '[other:%s]'%OWNER_B[:8] in admin['text']


def test_read_scrubs_secrets(tmp_path):
    path=tmp_path/'engine.log'
    path.write_text(_stamp()+' connect failed dsn=postgresql://u:p@host/db key=sk-abcdefghijklmnop1234\n')
    result=engine_log.read(OWNER_A,True,30,_resolver({}),path=path)
    assert 'u:p@host' not in result['text'] and 'sk-abcdefghijklmnop1234' not in result['text']


def test_read_missing_file_is_unavailable_not_error(tmp_path):
    result=engine_log.read(OWNER_A,True,30,_resolver({}),path=tmp_path/'none.log')
    assert result=={'available':False,'text':'','count':0,'path':str(tmp_path/'none.log')}


def test_resolver_failure_keeps_line_operator_only(tmp_path):
    path=tmp_path/'engine.log'
    path.write_text(_stamp()+' boom session=%s\n'%SESSION_A)
    def broken(session):raise RuntimeError('store down')
    customer=engine_log.read(OWNER_A,False,30,broken,path=path)
    assert customer['text']==''
    admin=engine_log.read(OWNER_A,True,30,broken,path=path)
    assert SESSION_A in admin['text']


def test_route_merges_engine_block(tmp_path,monkeypatch):
    import delivery_api.app as module
    from delivery_api.store import create_store
    store=create_store(str(tmp_path/'test.db'))
    monkeypatch.setattr(module,'store',store)
    monkeypatch.setenv('XDG_DATA_HOME',str(tmp_path))
    logs=tmp_path/'requi-re'/'logs';logs.mkdir(parents=True)
    (logs/'engine.log').write_text(_stamp()+' engine marker session=%s\n'%SESSION_A)
    actor=type('A',(),{'id':OWNER_A,'admin':True})
    result=module.server_log(30,actor())
    assert engine_log.HEADER in result['text'] and SESSION_A in result['text']
    assert result['engine_log']['available'] is True and result['engine_log']['lines']==1
