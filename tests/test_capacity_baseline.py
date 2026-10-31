"""Bounded disposable capacity driver, not a second provider stack."""
import importlib.util,os,subprocess,sys,json
from pathlib import Path
import pytest
ROOT=Path(__file__).parents[1]

def module():
    spec=importlib.util.spec_from_file_location('capacity_baseline',ROOT/'scripts/bench/capacity-baseline.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m


def test_baseline_refuses_external_database_before_starting(monkeypatch):
    m=module();monkeypatch.setenv('DELIVERY_DATABASE_URL','postgresql://do-not-open')
    with pytest.raises(ValueError,match='inherited'):m.fence()


def test_each_alert_boundary_is_explicit():
    m=module();row=dict(queue_ms_max=1000,pool_wait_ms_max=250,db_lock_waiters_max=0,host_memory_fraction_max=.85,db_connections_max=12,errors=[])
    assert not any(m.alerts(row).values())
    row.update(queue_ms_max=1001,pool_wait_ms_max=251,db_lock_waiters_max=1,host_memory_fraction_max=.86,db_connections_max=13,errors=['TimeoutError'])
    assert all(m.alerts(row).values())


def test_owned_baseline_runs_124_users_without_network(tmp_path):
    m=module();env={k:v for k,v in os.environ.items() if k not in m.FORBIDDEN};out=tmp_path/'capacity.json'
    result=subprocess.run([sys.executable,str(ROOT/'scripts/bench/capacity-baseline.py'),'--output',str(out),'--rounds','1'],env=env,capture_output=True,text=True,timeout=30)
    assert result.returncode==0,result.stdout+result.stderr
    report=json.loads(out.read_text());assert report['network']=='denied' and report['paid_calls']==0
    assert [r['users'] for r in report['cohorts']]==[1,2,4]
    assert [r['completed'] for r in report['cohorts']]==[1,2,4]
    assert all(not r['errors'] and r['db_connections_max']<=10 for r in report['cohorts'])
