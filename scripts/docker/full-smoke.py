"""Installed full-image proof. No provider key, no external network, no accounts.
Run inside the built image:  docker run --rm --network none --read-only --tmpfs /tmp python /app/full-smoke.py"""
import json,os,socket,subprocess,sys,tempfile,time,urllib.error,urllib.request


def free_port():
    with socket.socket() as s:
        s.bind(('127.0.0.1',0));return s.getsockname()[1]


def get(url,headers=None):
    return urllib.request.urlopen(urllib.request.Request(url,headers=headers or {}),timeout=3)


def main():
    scratch=tempfile.mkdtemp(prefix='full-smoke-')
    port=free_port()
    env={k:v for k,v in os.environ.items() if k in ('PATH','LANG')}
    env.update(HOME=scratch,XDG_DATA_HOME=scratch,VMODEL_ARTIFACTS_DIR=scratch+'/artifacts',PYTHONNOUSERSITE='1',
        DELIVERY_DATABASE_URL='sqlite:///'+scratch+'/smoke.db',DELIVERY_HOST='127.0.0.1',DELIVERY_PORT=str(port),
        DELIVERY_CONTROL_PLANE_ONLY='0',INTERVIEW_ENGINE_TOKEN='smoke-'+os.urandom(16).hex(),INTERVIEW_ENGINE_TENANTS='0',
        DELIVERY_COOKIE_SECURE='0',DELIVERY_ADMIN_TOKEN='smoke-admin-'+os.urandom(8).hex())
    here=os.path.dirname(os.path.abspath(__file__))
    log=open(scratch+'/entry.log','w+')
    proc=subprocess.Popen([sys.executable,os.path.join(here,'full_entry.py')],cwd=scratch,env=env,stdout=log,stderr=log)
    result={}
    try:
        for _ in range(240):
            if proc.poll() is not None:
                log.seek(0);raise RuntimeError('entry exited early: '+log.read()[-2500:])
            try:
                health=json.load(get(f'http://127.0.0.1:{port}/health'));break
            except OSError:time.sleep(0.5)
        else:raise RuntimeError('delivery health timeout')
        assert health.get('ok') is True and health.get('runtime_profile')!='control_plane_only',health
        result['delivery_health']=True
        try:get('http://127.0.0.1:8010/v1/model-routes/default');raise AssertionError('engine answered without its token')
        except urllib.error.HTTPError as e:assert e.code in (401,403),e.code
        result['engine_requires_token']=True
        r=subprocess.run([sys.executable,os.path.join(here,'full_entry.py'),'--healthcheck'],env={**env},capture_output=True,text=True)
        assert r.returncode==0,r.stderr
        result['healthcheck_command']=True
        # killing the engine must take the container down (restart policy then sees it)
        killed=0
        for pid in os.listdir('/proc'):
            if pid.isdigit() and int(pid)!=os.getpid():
                try:cmd=open('/proc/%s/cmdline'%pid,'rb').read().replace(b'\0',b' ')
                except OSError:continue
                if b'interview_engine.http_service' in cmd:os.kill(int(pid),9);killed+=1
        assert killed,'engine process not found'
        for _ in range(60):
            if proc.poll() is not None:break
            time.sleep(0.5)
        assert proc.poll() is not None and proc.returncode!=0,'entry did not stop after the engine died'
        result['engine_death_stops_container']=True
        result['provider_calls']=0
        print(json.dumps(result));return 0
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:proc.wait(timeout=10)
            except subprocess.TimeoutExpired:proc.kill()
        log.close()


if __name__=='__main__':sys.exit(main())
