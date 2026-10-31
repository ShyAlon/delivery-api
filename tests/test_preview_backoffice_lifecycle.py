"""No Docker, provider or DB: real private listener ownership and shutdown only."""
from pathlib import Path
import os,socket,subprocess
import pytest
ROOT=Path(__file__).parents[1]
SCRIPT=ROOT/'scripts/ops/preview-exec.sh'

def free_port():
    with socket.socket() as sock:sock.bind(('127.0.0.1',0));return sock.getsockname()[1]

def run_functions(tmp_path,port,body,timeout=15):
    s=SCRIPT.read_text();functions=s[s.index('stop_backoffice(){'):s.index('cleanup(){')]
    return subprocess.run(['bash','-c',functions+'\nscrub(){ cat; }; WORK="$1"; BACKOFFICE_PREVIEW_PORT="$2"; PORT="$3"; BACKOFFICE_PID=""; trap stop_backoffice EXIT\n'+body,'_',str(tmp_path),str(port),str(free_port())],cwd=ROOT,env=os.environ,capture_output=True,text=True,timeout=timeout)

def test_launch_health_proxy_origin_and_owned_cleanup(tmp_path):
    port=free_port();r=run_functions(tmp_path,port,'''start_backoffice || exit 5
[ -n "$BACKOFFICE_PID" ] || exit 6
kill -0 "$BACKOFFICE_PID" || exit 7
curl -fs "http://127.0.0.1:$BACKOFFICE_PREVIEW_PORT/" | grep -q backoffice-assets || exit 8
stop_backoffice
[ -z "$BACKOFFICE_PID" ] || exit 9
''')
    assert r.returncode==0,r.stdout+r.stderr
    with socket.socket() as sock:sock.bind(('127.0.0.1',port))


def test_port_collision_never_reuses_or_kills_an_existing_listener(tmp_path):
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0));sock.listen();port=sock.getsockname()[1]
        r=run_functions(tmp_path,port,'start_backoffice; result=$?; [ -z "$BACKOFFICE_PID" ] || exit 7; exit "$result"')
        assert r.returncode==1,r.stdout+r.stderr
        assert 'cannot own its private port' in r.stderr
        assert sock.getsockname()[1]==port


def test_failure_does_not_claim_ready_and_cleans_owned_process(tmp_path):
    # Broken Python module launch, while the port preflight remains real.
    venv=tmp_path/'bad-python';venv.mkdir();fake=venv/'python';fake.write_text('#!/bin/sh\nif [ "$1" = -m ]; then exit 17; fi\nexec '+str(ROOT/'.venv/bin/python')+' "$@"\n');fake.chmod(0o755)
    s=SCRIPT.read_text();functions=s[s.index('stop_backoffice(){'):s.index('cleanup(){')].replace('.venv/bin/python',str(fake))
    r=subprocess.run(['bash','-c',functions+'\nscrub(){ cat; }; WORK="$1"; BACKOFFICE_PREVIEW_PORT="$2"; PORT="$3"; BACKOFFICE_PID=""; start_backoffice; r=$?; [ -z "$BACKOFFICE_PID" ] || exit 7; exit "$r"','_',str(tmp_path),str(free_port()),str(free_port())],cwd=ROOT,capture_output=True,text=True,timeout=15)
    assert r.returncode==1,r.stdout+r.stderr
    assert 'did not become healthy' in r.stdout


def test_preview_cleanup_always_stops_backoffice_even_if_container_retained():
    s=SCRIPT.read_text();cleanup=s[s.index('cleanup(){'):s.index('\nSTEPS_TOTAL=')]
    assert cleanup.index('stop_backoffice')<cleanup.index('KEEP_FOR_INSPECTION')
    assert s.index('start_backoffice || abort')<s.index('echo "PREVIEW-EXEC READY:')
