"""Actual signed-in HTTP, real child graph and disposable PG; fixture vendor only."""
import os
from pathlib import Path
import subprocess
import sys
from test_fresh_start_acceptance import test_fresh_zero_credit_http_start as run_fresh_start


def test_owner_script_real_http_child_cycle_with_fixture_provider(pg_workspace_factory,pg_task_app,pg_runtime_environment,monkeypatch,tmp_path):
    # Retained test-money evidence must survive this journey, yet must not enter
    # other fixtures' platform-wide expense guards. A separate owned disposable
    # server is the isolation boundary, not deleting settled money or raising caps.
    if os.environ.get('REQUI_OWNER_ACCEPTANCE_ISOLATED')=='1':
        run_fresh_start(pg_workspace_factory,pg_task_app,pg_runtime_environment,monkeypatch,tmp_path,True,owner_acceptance=True)
        return
    env=dict(os.environ,REQUI_OWNER_ACCEPTANCE_ISOLATED='1')
    for name in ('DELIVERY_TEST_POSTGRES_DSN','DELIVERY_DATABASE_URL'):
        env.pop(name,None)
    result=subprocess.run([sys.executable,'-m','pytest','-q',str(Path(__file__).resolve())],
                          cwd=Path(__file__).resolve().parents[1],env=env,capture_output=True,text=True,timeout=90)
    assert result.returncode==0,result.stdout+'\n'+result.stderr
