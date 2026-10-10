from pathlib import Path
import json,os,subprocess
import pytest
ROOT=Path(__file__).parents[1]


def fixture(tmp_path,mode):
    bins=tmp_path/'bin';bins.mkdir();log=tmp_path/'calls';state=tmp_path/'state.json'
    state.write_text(json.dumps({'requi-delivery':'healthy'}))
    docker=bins/'docker'
    docker.write_text('''#!/usr/bin/env python3
import os,sys,json,pathlib
args=sys.argv[1:];p=pathlib.Path(os.environ['STATE']);s=json.loads(p.read_text())
with open(os.environ['LOG'],'a') as f:f.write(json.dumps(args)+'\\n')
code=0
if args[0]=='inspect':
 name=args[-1]
 if name not in s:code=1
 elif '--format' in args:print(s[name])
elif args[0]=='run':
 name=args[args.index('--name')+1];s[name]='healthy'
 if os.environ['MODE']=='candidate-fail' and name.endswith('-candidate'):s[name]='unhealthy'
 if os.environ['MODE']=='cutover-fail' and name=='requi-delivery':s[name]='unhealthy'
elif args[0]=='rm':s.pop(args[-1],None)
elif args[0]=='rename':
 if os.environ['MODE']=='rename-fail' and args[2]=='requi-delivery-previous':code=1
 else:s[args[2]]=s.pop(args[1])
p.write_text(json.dumps(s));sys.exit(code)
''');docker.chmod(0o755)
    envfile=tmp_path/'reviewed.env';envfile.write_text('DELIVERY_DATABASE_URL=postgresql://fixture\nDELIVERY_SUPABASE_URL=https://fixture.invalid\nDELIVERY_SUPABASE_ANON_KEY=fixture-public\n');envfile.chmod(0o600)
    env=os.environ|{'PATH':str(bins)+os.pathsep+os.environ['PATH'],'MODE':mode,'LOG':str(log),'STATE':str(state)}
    result=subprocess.run(['bash',str(ROOT/'scripts/deploy-server.sh'),'example.invalid/control@sha256:'+'a'*64,str(envfile)],env=env,capture_output=True,text=True)
    calls=[json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
    return result,calls,json.loads(state.read_text())


def test_candidate_health_failure_preserves_running_service(tmp_path):
    result,calls,state=fixture(tmp_path,'candidate-fail')
    assert result.returncode==1 and 'requi-delivery' in state
    assert not any(c[0] in ('stop','rename') for c in calls)
    run=next(c for c in calls if c[0]=='run')
    assert '-p' not in run and '--read-only' in run and '--cap-drop' in run


def test_failed_cutover_restores_original_container_not_reconstructed_config(tmp_path):
    result,calls,state=fixture(tmp_path,'cutover-fail')
    assert result.returncode==1 and state['requi-delivery']=='healthy'
    assert ['rename','requi-delivery-previous','requi-delivery'] in calls
    assert ['start','requi-delivery'] in calls


def test_success_is_loopback_only_retains_previous_container_and_never_prunes(tmp_path):
    result,calls,state=fixture(tmp_path,'success')
    assert result.returncode==0 and 'requi-delivery-previous' in state
    runs=[c for c in calls if c[0]=='run'];assert len(runs)==2
    assert '127.0.0.1:8000:8000' in runs[-1]
    assert not any(c[0]=='image' or c[0]=='push' for c in calls)
    assert all('--env-file' in c and '--security-opt' in c for c in runs)


def test_no_mutable_image_or_missing_environment_can_start_docker(tmp_path):
    result=subprocess.run(['bash',str(ROOT/'scripts/deploy-server.sh'),'repo:main',str(tmp_path/'absent')],capture_output=True)
    assert result.returncode!=0


def test_failed_original_rename_never_removes_or_stops_original(tmp_path):
    result,calls,state=fixture(tmp_path,'rename-fail')
    assert result.returncode==1 and state['requi-delivery']=='healthy'
    assert not any(c[0]=='stop' for c in calls)
    assert not any(c[0]=='rm' and c[-1]=='requi-delivery' for c in calls)
