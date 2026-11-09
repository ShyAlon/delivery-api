import importlib.util
import os
from pathlib import Path
import socket
import sys
import pytest

D=Path(__file__).resolve().parents[1]/'scripts/docker';sys.path.insert(0,str(D))
spec=importlib.util.spec_from_file_location('private_backoffice',D/'private_backoffice.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)


def free():
    with socket.socket() as listener:listener.bind(('127.0.0.1',0));return listener.getsockname()[1]


def test_real_private_listener_owned_start_health_close(tmp_path,monkeypatch):
    monkeypatch.setenv('PYTHONPATH',str(D.parents[1]/'src'))
    listener=m.PrivateBackoffice(free(),free(),tmp_path/'private.log')
    try:
        listener.start();assert listener.process.poll() is None
    finally:listener.close()
    assert listener.process is None and listener.log is None
    with socket.socket() as check:
        check.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
        check.bind(('127.0.0.1',listener.port))
    assert (tmp_path/'private.log').stat().st_mode&0o777==0o600


def test_collision_never_takes_over_listener(tmp_path):
    with socket.socket() as existing:
        existing.bind(('127.0.0.1',0));existing.listen();port=existing.getsockname()[1]
        listener=m.PrivateBackoffice(port,free(),tmp_path/'none',bind_wait_seconds=.2)
        with pytest.raises(m.WorkflowError,match='existing listener untouched'):listener.start()
        assert listener.process is None and not (tmp_path/'none').exists()


@pytest.mark.parametrize('port',[80,8000,8010,8021])
def test_conflicts_refused(tmp_path,port):
    with pytest.raises(m.WorkflowError):m.PrivateBackoffice(port,8021,tmp_path/'log')


def _time_wait_port(reuse_old=True):
    listener=socket.socket()
    if reuse_old:listener.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
    listener.bind(('127.0.0.1',0));listener.listen();port=listener.getsockname()[1]
    client=socket.create_connection(('127.0.0.1',port));accepted,_=listener.accept()
    accepted.close();client.close();listener.close()
    return port


def test_probe_binds_across_cooldown(tmp_path):
    m.PrivateBackoffice(_time_wait_port(),free(),tmp_path/'none')._own_port()


def test_conflict_fail_names_holder(tmp_path):
    with socket.socket() as existing:
        existing.bind(('127.0.0.1',0));existing.listen();port=existing.getsockname()[1]
        listener=m.PrivateBackoffice(port,free(),tmp_path/'none',bind_wait_seconds=.2)
        with pytest.raises(m.WorkflowError) as error:listener.start()
    text=str(error.value)
    assert 'existing listener untouched' in text and str(os.getpid()) in text and not (tmp_path/'none').exists()


def test_fail_without_holder_reports_port_state(tmp_path,monkeypatch):
    port=free();upstream=free()
    def broken(*args,**kwargs):raise OSError('denied')
    monkeypatch.setattr(m.socket,'socket',broken)
    listener=m.PrivateBackoffice(port,upstream,tmp_path/'none',bind_wait_seconds=.1)
    with pytest.raises(m.WorkflowError) as error:listener.start()
    import re
    assert re.search(r'no current listener|busy',str(error.value))


def _stale_backoffice(port,upstream):
    import subprocess,time,urllib.request
    env=dict(os.environ,PYTHONPATH=str(D.parents[1]/'src'),BACKOFFICE_HOST='127.0.0.1',BACKOFFICE_PORT=str(port),
        BACKOFFICE_SERVICE_URL='http://127.0.0.1:'+str(upstream))
    process=subprocess.Popen([sys.executable,'-m','delivery_api.backoffice'],env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    for _ in range(100):
        try:
            urllib.request.urlopen('http://127.0.0.1:'+str(port)+'/health',timeout=.5).read();return process
        except OSError:time.sleep(.1)
    process.kill();raise AssertionError('stale back office did not start')


def test_stale_own_backoffice_is_recycled_not_a_failure(tmp_path,monkeypatch):
    # Real failure: a Back office left by an earlier preview run held 8020 and
    # the rebuild aborted with "existing listener untouched".
    monkeypatch.setenv('PYTHONPATH',str(D.parents[1]/'src'))
    port=free();upstream=free();stale=_stale_backoffice(port,upstream)
    listener=m.PrivateBackoffice(port,upstream,tmp_path/'private.log')
    try:
        listener.start()
        assert stale.wait(timeout=10) is not None
        assert listener.process.poll() is None and listener.process.pid!=stale.pid
    finally:
        listener.close()
        if stale.poll() is None:stale.kill()
