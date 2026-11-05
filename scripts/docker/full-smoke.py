"""Installed full-image proof. No provider key, no external network, no accounts.
Run the test-only PG derivative under owned-pg-smoke.py; see scripts/docker/README.md."""
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
        DELIVERY_DATABASE_URL=os.environ.get('DELIVERY_TEST_POSTGRES_URL',''),DELIVERY_HOST='127.0.0.1',DELIVERY_PORT=str(port),
        DELIVERY_CONTROL_PLANE_ONLY='0',INTERVIEW_ENGINE_TOKEN='smoke-'+os.urandom(16).hex(),INTERVIEW_ENGINE_TENANTS='1',
        DELIVERY_COOKIE_SECURE='0',DELIVERY_ADMIN_TOKEN='smoke-admin-'+os.urandom(8).hex(),
        INTERVIEW_ENGINE_SESSIONS_FILE='0',DELIVERY_SUPABASE_URL='https://isolated-smoke.supabase.co',DELIVERY_SUPABASE_ANON_KEY='hermetic-public-key')
    if not env['DELIVERY_DATABASE_URL'].startswith(('postgresql://','postgres://')):
        raise ValueError('explicit disposable DELIVERY_TEST_POSTGRES_URL required for full-image smoke; no default or live database')
    here=os.path.dirname(os.path.abspath(__file__))
    log=open(scratch+'/entry.log','w+')
    proc=None
    stage='spawning the full service entry'
    def entry_tail():
        try:
            log.flush();log.seek(0);return log.read()[-2500:]
        except OSError:
            return ''
    try:
        print('smoke stage: %s' % stage,flush=True)
        proc=subprocess.Popen([sys.executable,os.path.join(here,'full_entry.py')],cwd=scratch,env=env,stdout=log,stderr=log)
        result={}
        stage='waiting for delivery health'
        print('smoke stage: %s' % stage,flush=True)
        for _ in range(240):
            if proc.poll() is not None:
                raise RuntimeError('entry exited early (code %s)' % proc.returncode)
            try:
                health=json.load(get(f'http://127.0.0.1:{port}/health'));break
            except OSError:time.sleep(0.5)
        else:raise RuntimeError('delivery health timeout after 120s')
        assert health.get('ok') is True and health.get('runtime_profile')!='control_plane_only',health
        result['delivery_health']=True
        stage='serving every shipped JS asset whole and parsed'
        print('smoke stage: %s' % stage,flush=True)
        import pathlib,shutil
        import delivery_api
        web=pathlib.Path(delivery_api.__file__).parent/'web'
        assets=sorted(web.glob('*.js'));assert assets,'no shipped JS assets found'
        for f in assets:
            body=get(f'http://127.0.0.1:{port}/assets/{f.name}').read()
            assert body==f.read_bytes(),f'/assets/{f.name}: served bytes differ from the shipped file'
        result['js_assets_served_whole']=len(assets)
        node=shutil.which('node')
        if node:
            bad=[]
            for f in assets:
                copy=pathlib.Path(scratch)/(f.stem+'.mjs');copy.write_bytes(f.read_bytes())  # real copy: a symlink resolves to .js and node picks script goal
                if subprocess.run([node,'--check',str(copy)],capture_output=True).returncode:bad.append(f.name)
            assert not bad,'shipped JS that does not parse: '+', '.join(bad)
            result['js_assets_parse']=len(assets)
        else:
            print('smoke note: node is not in the image - JS SYNTAX not verified here (preview-exec step 2 and the pytest gate cover it where node exists)',flush=True)
        stage='running the container healthcheck command'
        print('smoke stage: %s' % stage,flush=True)
        r=subprocess.run([sys.executable,os.path.join(here,'full_entry.py'),'--healthcheck'],env={**env},capture_output=True,text=True)
        assert r.returncode==0,r.stderr
        result['healthcheck_command']=True
        # The shared runtime has no permanent engine service. Delivery owns
        # per-job children; a dead delivery parent must fail the container.
        stage='terminating delivery and requiring the entry to fail'
        print('smoke stage: %s' % stage,flush=True)
        killed=0
        for pid in os.listdir('/proc'):
            if pid.isdigit() and int(pid)!=os.getpid():
                try:args=open('/proc/%s/cmdline'%pid,'rb').read().split(b'\0')
                except OSError:continue
                if len(args)>=3 and args[1:3]==[b'-m',b'delivery_api']:
                    os.kill(int(pid),15);killed+=1
        assert killed==1,'exact delivery parent not found or ambiguous'
        for _ in range(60):
            if proc.poll() is not None:break
            time.sleep(0.5)
        assert proc.poll() is not None and proc.returncode!=0,'entry did not fail after delivery exited'
        result['delivery_death_stops_container']=True
        result['provider_calls']=0
        print(json.dumps(result));return 0
    except BaseException as error:
        # One honest failure line: the stage, the exception, and the entry's own report.
        print('SMOKE FAILED at stage [%s]: %s: %s' % (stage,type(error).__name__,str(error)[:500]),file=sys.stderr)
        tail=entry_tail()
        if tail.strip():print('entry log tail:\n'+tail,file=sys.stderr)
        return 1
    finally:
        if proc is not None and proc.poll() is None:
            proc.terminate()
            try:proc.wait(timeout=10)
            except subprocess.TimeoutExpired:proc.kill()
        log.close()


if __name__=='__main__':sys.exit(main())
