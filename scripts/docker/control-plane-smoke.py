"""Installed delivery-only wheel proof. No accounts, external network or engines."""
import importlib.util,json,os,socket,subprocess,tempfile,time,urllib.request,urllib.error
from pathlib import Path


def main():
    for module in ('interview_engine','spec_generator','openai','ollama','langchain_core'):
        if importlib.util.find_spec(module) is not None:raise RuntimeError('engine/provider package present')
    with tempfile.TemporaryDirectory(prefix='control-plane-smoke-') as scratch:
        with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
        env={k:v for k,v in os.environ.items() if k in ('PATH','LANG','SYSTEMROOT')}
        env.update(HOME=scratch,XDG_DATA_HOME=scratch,PYTHONNOUSERSITE='1',DELIVERY_DATABASE_URL=os.environ.get('DELIVERY_TEST_POSTGRES_URL',''),
            DELIVERY_SUPABASE_URL='https://isolated-smoke.supabase.co',DELIVERY_SUPABASE_ANON_KEY='hermetic-public-key',
            DELIVERY_HOST='127.0.0.1',DELIVERY_PORT=str(port))
        if not env['DELIVERY_DATABASE_URL'].startswith(('postgresql://','postgres://')):
            raise ValueError('explicit disposable DELIVERY_TEST_POSTGRES_URL required; no database default')
        log=open(Path(scratch)/'server.log','w+')
        import sys
        proc=subprocess.Popen([sys.executable,'-m','delivery_api.control_plane'],cwd=scratch,env=env,stdout=log,stderr=log)
        try:
            for i in range(60):
                if proc.poll() is not None:
                    log.seek(0);raise RuntimeError('installed wheel startup failed: '+log.read()[-2000:])
                try:
                    with urllib.request.urlopen(f'http://127.0.0.1:{port}/health',timeout=1) as response:health=json.load(response)
                    break
                except OSError:time.sleep(.1)
            else:raise RuntimeError('health timeout')
            assert health=={'ok':True,'runtime_profile':'control_plane_only','engine_execution':False,'payments_enabled':False}
            with urllib.request.urlopen(f'http://127.0.0.1:{port}/',timeout=2) as response:assert response.status==200
            try:urllib.request.urlopen(urllib.request.Request(f'http://127.0.0.1:{port}/api/tasks',data=b'{}',headers={'Content-Type':'application/json'}),timeout=2)
            except urllib.error.HTTPError as error:assert error.code==503
            else:raise AssertionError('mutations not fenced')
            print(json.dumps({'installed_wheel_health':True,'static_ui':True,'mutations_fenced':True,'engine_provider_packages_absent':True,'container_execution_verified':False}))
        finally:
            proc.terminate()
            try:proc.wait(timeout=5)
            except subprocess.TimeoutExpired:proc.kill();proc.wait(timeout=5)
            log.close()


if __name__=='__main__':main()
