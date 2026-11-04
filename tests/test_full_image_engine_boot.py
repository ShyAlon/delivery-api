"""The full image entry boots the one resident delivery parent.

Regresses the 2026-10-24 deploy smoke failure: after interview-engine PR #49 the
engine refuses every request (HTTP 500) until session persistence is chosen, and
the full image never chose one. These checks run the actual full_entry.py as a
subprocess: the fail-fast check is hermetic; the boot check needs the engine
source (DELIVERY_TEST_ENGINE_SOURCE) and is marked engine_integration like the
other cross-repo boots.
"""
import json,os,subprocess,sys,time,urllib.error,urllib.request
from pathlib import Path

import pytest

ENTRY=Path(__file__).resolve().parents[1]/'scripts'/'docker'/'full_entry.py'


def _base_env(tmp_path):
    return {'PATH':os.environ.get('PATH',''),'HOME':str(tmp_path),'XDG_DATA_HOME':str(tmp_path),
        'VMODEL_ARTIFACTS_DIR':str(tmp_path/'artifacts'),'PYTHONNOUSERSITE':'1',
        'DELIVERY_DATABASE_URL':'sqlite:///'+str(tmp_path/'smoke.db'),'DELIVERY_HOST':'127.0.0.1',
        'DELIVERY_CONTROL_PLANE_ONLY':'0','INTERVIEW_ENGINE_TOKEN':'boot-test-'+('x'*24),
        'INTERVIEW_ENGINE_TENANTS':'0','DELIVERY_COOKIE_SECURE':'0','DELIVERY_ADMIN_TOKEN':'boot-admin-'+'y'*16}


def test_entry_refuses_start_without_a_session_persistence_choice(tmp_path):
    """sqlite delivery DSN and no explicit choice: the entry must fail clear at
    start (exit 2, naming the variables), never boot an engine that 500s later."""
    result=subprocess.run([sys.executable,str(ENTRY)],env=_base_env(tmp_path),capture_output=True,text=True,timeout=30)
    assert result.returncode==2,result
    assert 'INTERVIEW_ENGINE_SESSIONS_DSN' in result.stderr and 'INTERVIEW_ENGINE_SESSIONS_FILE' in result.stderr,result.stderr


@pytest.mark.engine_integration
def test_entry_boots_delivery_parent_with_owned_pg(tmp_path,pg_runtime_environment):
    """Real entry boots delivery; anonymous health never grants signed access."""
    engine_source=os.environ.get('DELIVERY_TEST_ENGINE_SOURCE')
    if not engine_source:pytest.skip('explicit engine source required')
    import socket
    with socket.socket() as s:
        s.bind(('127.0.0.1',0));delivery_port=s.getsockname()[1]
    env=_base_env(tmp_path)|pg_runtime_environment()|{'DELIVERY_PORT':str(delivery_port),'INTERVIEW_ENGINE_SESSIONS_FILE':'0','INTERVIEW_ENGINE_TENANTS':'1',
        'PYTHONPATH':str(Path(engine_source)/'src')+os.pathsep+str(Path(__file__).resolve().parents[1]/'src')}
    proc=subprocess.Popen([sys.executable,str(ENTRY)],env=env,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True)
    try:
        deadline=time.time()+120
        while time.time()<deadline:
            if proc.poll() is not None:
                raise AssertionError('entry exited early: '+proc.stdout.read()[-12000:])
            try:
                with urllib.request.urlopen(f'http://127.0.0.1:{delivery_port}/health',timeout=3) as r:
                    health=json.load(r)
                break
            except OSError:time.sleep(0.5)
        else:raise AssertionError('delivery health timeout')
        assert health.get('ok') is True and health.get('runtime_profile')!='control_plane_only',health
        # Child phases are job-scoped, not an always-on HTTP daemon. Signed
        # reads remain protected at the actual service boundary.
        try:
            urllib.request.urlopen(f'http://127.0.0.1:{delivery_port}/api/tasks',timeout=3)
            raise AssertionError('signed task data answered anonymously')
        except urllib.error.HTTPError as e:
            assert e.code in (401,403),e.code
        children=[]
        for entry in Path('/proc').iterdir():
            if not entry.name.isdigit():continue
            try:
                status=(entry/'status').read_text()
                if f'PPid:\t{proc.pid}\n' in status:
                    children.append((entry/'cmdline').read_bytes().split(b'\0'))
            except OSError:continue
        assert len(children)==1,children
        assert children[0][1:3]==[b'-m',b'delivery_api'],children
    finally:
        proc.terminate()
        try:proc.wait(timeout=20)
        except subprocess.TimeoutExpired:proc.kill()
