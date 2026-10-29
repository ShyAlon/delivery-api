"""A killed test process must not leave its disposable PostgreSQL behind."""
import json
import os
from pathlib import Path
import signal
import selectors
import subprocess
import sys
import time

import pytest


@pytest.mark.parametrize('exit_signal', [signal.SIGTERM, signal.SIGKILL])
@pytest.mark.parametrize('whole_group', [False, True])
def test_disposable_server_stops_after_parent_abnormal_exit(exit_signal, whole_group):
    import psutil
    import pgserver
    code = '''
import json,sys,time
sys.path.insert(0,'tests')
import pg_harness
pg_harness.start()
assert pg_harness.STATE['status']=='running',pg_harness.STATE
from psycopg.conninfo import conninfo_to_dict
from pathlib import Path
scratch=Path(conninfo_to_dict(pg_harness.STATE['dsn'])['host'])
print(json.dumps({'scratch':str(scratch),'pid':int((scratch/'postmaster.pid').read_text().splitlines()[0])}),flush=True)
while True:time.sleep(1)
'''
    env = {k:v for k,v in os.environ.items() if k not in ('DELIVERY_TEST_POSTGRES_DSN','DELIVERY_TEST_NO_POSTGRES')}
    process = subprocess.Popen([sys.executable,'-c',code],cwd=Path(__file__).parents[1],env=env,
                               stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,start_new_session=True)
    evidence = None
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            assert selector.select(timeout=15), 'harness did not publish startup evidence'
        evidence = json.loads(process.stdout.readline())
        scratch = Path(evidence['scratch'])
        assert scratch.parent == Path('/tmp') and scratch.name.startswith('delivery-api-pg-')
        assert psutil.pid_exists(evidence['pid'])
        if whole_group:os.killpg(process.pid, exit_signal)
        else:process.send_signal(exit_signal)
        process.wait(timeout=5)
        def running():
            try:return psutil.Process(evidence['pid']).status() != psutil.STATUS_ZOMBIE
            except psutil.NoSuchProcess:return False
        deadline = time.monotonic()+10
        while running() and time.monotonic()<deadline:time.sleep(.05)
        assert not running(), f"orphan disposable PG pid={evidence['pid']} scratch={scratch}"
    finally:
        if process.poll() is None:process.kill();process.wait(timeout=5)
        if evidence:
            ctl=Path(pgserver.__file__).parent/'pginstall/bin/pg_ctl'
            subprocess.run([str(ctl),'-D',evidence['scratch'],'stop','-m','fast','-w','-t','5'],
                           capture_output=True,text=True)
        process.stdout.close();process.stderr.close()
