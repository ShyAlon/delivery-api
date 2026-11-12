"""The diagnosed timeout must fail once, not cascade into repeat DB waits."""
import importlib.util
from pathlib import Path
import sys

D=Path(__file__).resolve().parents[1]/'scripts/docker'
sys.path.insert(0,str(D))
from test_startup_preflight import healthy_env,engine_modules,fake_import
spec=importlib.util.spec_from_file_location('startup_failure_preflight',D/'preflight.py')
m=importlib.util.module_from_spec(spec);sys.modules[spec.name]=m;spec.loader.exec_module(m)


def test_connectivity_failure_is_one_root_cause_with_dependent_skips(tmp_path,capsys):
 calls=[]
 def connect(dsn):
  calls.append(dsn)
  raise TimeoutError('database connection timeout')
 report=m.run(healthy_env(tmp_path),connect=connect,import_module=fake_import(engine_modules()))
 assert len(calls)==1
 assert report.refused
 checks={c.name:c for c in report.checks}
 assert checks['database-role-claims'].status=='fail'
 for name in ('resource-metrics-store','engine-quality-store','required-tables','feature-tables'):
  assert checks[name].status=='skip'
 assert checks['engine-compatible'].status=='pass'
 assert 'root cause: database unreachable' in report.render_text().lower()
 assert 'timeout' in report.render_text().lower()
 output=capsys.readouterr().err
 assert 'database-role-claims' in output and 'elapsed' in output


def test_total_connect_deadline_interrupts_driver_and_restores_alarm(monkeypatch):
 import signal,time,psycopg
 original=signal.getsignal(signal.SIGALRM);original_timer=signal.getitimer(signal.ITIMER_REAL)[0];calls=[]
 def blackhole(dsn,**kwargs):
  calls.append((dsn,kwargs));time.sleep(2)
 monkeypatch.setattr(psycopg,'connect',blackhole)
 started=time.monotonic()
 import pytest
 with pytest.raises(TimeoutError):m._real_connect('postgresql://fixture/db?sslmode=verify-full&options=-c%20application_name%3Dfixture',timeout=.05)
 assert time.monotonic()-started<.5
 assert signal.getsignal(signal.SIGALRM)==original
 remaining=signal.getitimer(signal.ITIMER_REAL)[0]
 assert (remaining==0 if original_timer==0 else original_timer-.5<remaining<=original_timer)
 assert calls[0][0].endswith('options=-c%20application_name%3Dfixture')
 assert 'application_name=fixture' in calls[0][1]['options']


def test_launcher_capture_redacts_state_logs_and_persisted_markers(tmp_path,capsys):
 import startup_failure,json
 secret='private-token-123';dsn='postgresql://app:pass-123@fixture/db'
 def command(args,**kwargs):
  if 'inspect' in args:return json.dumps({'Image':'sha256:fixture','Config':{'Env':['PRIVATE=should-not-export']},'State':{'Running':False,'Status':'exited','ExitCode':2,'Error':secret,'OOMKilled':False,'Health':{'Status':'unhealthy','Log':[{'Output':'password=pass-123 Connection refused'}]}}})
  if 'logs' in args:return 'root cause: database unreachable (timeout)\n'+secret+' '+dsn+' password=pass-123'
  if 'cp' in args:Path(args[-1]).write_text(json.dumps({'version':1,'stages':[{'stage':'preflight-refused','token':secret}]}));return ''
 evidence=startup_failure.capture('owned',tmp_path/'evidence',{'INTERVIEW_ENGINE_TOKEN':secret,'DELIVERY_DATABASE_URL':dsn},command=command)
 stored=(tmp_path/'evidence/startup-failure.json').read_text();out=capsys.readouterr().err
 for value in (secret,dsn,'pass-123','PRIVATE=should-not-export'):
  assert value not in stored and value not in out
 assert json.loads(stored)['state']['ExitCode']==2
 assert 'database unreachable' in evidence['probable_cause']
 assert (tmp_path/'evidence/startup-failure.json').stat().st_mode&0o777==0o600


def test_empty_log_uses_persisted_timeout_and_oom_is_separate(tmp_path,capsys):
 import startup_failure,json
 for oom in (False,True):
  def command(args,**kwargs):
   if 'inspect' in args:return json.dumps({'State':{'Running':False,'ExitCode':137 if oom else 2,'OOMKilled':oom}})
   if 'logs' in args:return ''
   if 'cp' in args:Path(args[-1]).write_text(json.dumps({'version':1,'stages':[{'stage':'database-prerequisite-refused','error_kind':'timeout'}]}));return ''
  result=startup_failure.capture('owned',tmp_path/str(oom),{},command=command)
  assert ('memory limit' if oom else 'database unreachable') in result['probable_cause']


def test_older_image_and_capture_failures_do_not_invent_a_db_cause(tmp_path):
 import startup_failure,json
 def command(args,**kwargs):
  if 'inspect' in args:return json.dumps({'State':{'Running':True,'ExitCode':0,'Health':{'Status':'unhealthy'}}})
  raise RuntimeError('unavailable')
 result=startup_failure.capture('owned',tmp_path,{},command=command)
 assert 'still running' in result['probable_cause'] and 'database' not in result['probable_cause']
 assert len(result['capture_errors'])==3


def test_tcp_probe_bounds_dns_without_printing_dsn():
 import startup_network,subprocess
 def hung(args,**kwargs):
  assert 'secret' not in ' '.join(args) and kwargs['input']=='postgresql://u:secret@fixture/db'
  assert kwargs['timeout']==5
  raise subprocess.TimeoutExpired(args,5)
 assert startup_network.probe('postgresql://u:secret@fixture/db',run=hung)['reason']=='timeout'
 assert startup_network.endpoint('postgresql:///fixture?host=/owned/socket') is None


def test_host_reachability_failure_preserves_old_preview(tmp_path,monkeypatch):
 import startup_network
 from test_local_image_preview import _apply_stubs,m as preview
 import pytest
 value,env,runs=_apply_stubs(tmp_path,monkeypatch,{'Running':True},True)
 monkeypatch.setattr(startup_network,'probe',lambda dsn:{'status':'unreachable','reason':'timeout'})
 with pytest.raises(preview.WorkflowError,match='preview not replaced'):
  preview.preview(value,env_file=str(env),apply=True)
 assert not any('stop' in args or 'rename' in args or 'run' in args for args in runs)


def test_real_full_entry_times_out_once_emits_and_persists_refusal(tmp_path):
 """Real driver + real startup CLI against a disposable TCP blackhole, no PG/account."""
 import os,socket,subprocess,threading,time,json
 with socket.socket() as listener:
  listener.bind(('127.0.0.1',0));listener.listen();listener.settimeout(.1)
  stop=threading.Event();accepted=[]
  def blackhole():
   while not stop.is_set():
    try:
     conn,_=listener.accept();accepted.append(conn)
    except socket.timeout:continue
  worker=threading.Thread(target=blackhole);worker.start()
  env=dict(os.environ,DELIVERY_DATABASE_URL=f'postgresql://fictional:fictional-password@127.0.0.1:{listener.getsockname()[1]}/fixture?sslmode=disable',INTERVIEW_ENGINE_TOKEN='fictional-private-token-123456',VMODEL_ARTIFACTS_DIR=str(tmp_path/'artifacts'),XDG_DATA_HOME=str(tmp_path),REQUI_STARTUP_EVIDENCE=str(tmp_path/'requi-startup.json'))
  started=time.monotonic()
  try:result=subprocess.run([sys.executable,str(D/'full_entry.py')],env=env,capture_output=True,text=True,timeout=20)
  finally:
   stop.set();worker.join(timeout=1)
   for conn in accepted:conn.close()
 assert result.returncode==2,result.stderr
 assert len(accepted)==1 and time.monotonic()-started<15
 assert 'entrypoint' in result.stderr and 'database-role-claims' in result.stderr
 assert 'root cause: database unreachable (timeout)' in result.stdout
 assert 'full service not started' in result.stderr and 'fictional-password' not in result.stdout+result.stderr
 report=json.loads((tmp_path/'requi-preflight.json').read_text())
 assert report['status']=='fail' and '4 dependent checks skipped' in report['root_cause']
 stages=json.loads((tmp_path/'requi-startup.json').read_text())['stages']
 assert stages[-1]['stage']=='preflight-refused'
 assert any(s.get('check')=='database-role-claims' and s.get('status')=='started' for s in stages)


def test_startup_marker_and_report_keep_private_values_out(tmp_path,monkeypatch):
 from delivery_api.startup_log import stage,persist_report
 import json
 monkeypatch.setenv('REQUI_STARTUP_EVIDENCE',str(tmp_path/'requi-startup.json'))
 monkeypatch.setenv('DELIVERY_DATABASE_URL','postgresql://app:odd-secret-password@fixture/db')
 stage('test-check',check='database-role-claims',status='started',password='never-written')
 persist_report({'status':'fail','checks':[{'found':'postgresql://app:odd-secret-password@fixture/db failed with odd-secret-password'}]})
 assert 'odd-secret-password' not in (tmp_path/'requi-preflight.json').read_text()
 assert 'never-written' not in (tmp_path/'requi-startup.json').read_text()
 assert (tmp_path/'requi-startup.json').stat().st_mode&0o777==0o600


def test_restart_does_not_reuse_prior_refusal(tmp_path,monkeypatch):
 from delivery_api.startup_log import stage
 import json
 monkeypatch.setenv('REQUI_STARTUP_EVIDENCE',str(tmp_path/'requi-startup.json'))
 stage('entrypoint');stage('database-prerequisite-refused',error_kind='timeout')
 first=json.loads((tmp_path/'requi-startup.json').read_text())['run_id']
 stage('entrypoint')
 second=json.loads((tmp_path/'requi-startup.json').read_text())
 assert second['run_id']!=first and [row['stage'] for row in second['stages']]==['entrypoint']


def test_capture_rejects_stale_report_and_malformed_stage(tmp_path):
 import startup_failure,json
 for malformed in (False,True):
  def command(args,**kwargs):
   if 'inspect' in args:return json.dumps({'State':{'Running':True,'ExitCode':0}})
   if 'logs' in args:return ''
   if 'cp' in args:
    value=({'version':1,'run_id':'new-run','stages':['bad'] if malformed else [{'stage':'entrypoint'}]} if 'requi-startup.json' in args[-2] else {'startup_run_id':'old-run','status':'fail','checks':[],'root_cause':'old database timeout'})
    Path(args[-1]).write_text(json.dumps(value));return ''
  evidence=startup_failure.capture('owned',tmp_path/str(malformed),{},command=command)
  assert 'preflight' not in evidence
  assert 'still running' in evidence['probable_cause']
