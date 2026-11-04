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
        listener=m.PrivateBackoffice(port,free(),tmp_path/'none')
        with pytest.raises(m.WorkflowError,match='existing listener untouched'):listener.start()
        assert listener.process is None and not (tmp_path/'none').exists()


@pytest.mark.parametrize('port',[80,8000,8010,8021])
def test_conflicts_refused(tmp_path,port):
    with pytest.raises(m.WorkflowError):m.PrivateBackoffice(port,8021,tmp_path/'log')
