"""Full-service launcher: the interview engine (loopback only) and the delivery API in one container.

Either process exiting stops the other and fails the container, so the restart policy sees it.
`--healthcheck` is the container health probe. No provider is called by this file."""
import json,os,signal,socket,subprocess,sys,time,urllib.request

ENGINE_HOST='127.0.0.1'
ENGINE_PORT=8010
DEFAULT_TOKEN='local-interview-engine-token'


SESSIONS_DSN_ENV='INTERVIEW_ENGINE_SESSIONS_DSN'
SESSIONS_FILE_ENV='INTERVIEW_ENGINE_SESSIONS_FILE'


def engine_env(env):
    """Environment for the engine process. The engine (interview-engine PR #49)
    refuses to serve until session persistence is chosen explicitly: a sessions
    DSN, or INTERVIEW_ENGINE_SESSIONS_FILE=1. The full service always has the
    delivery Postgres, so derive the engine's DSN from it unless the operator
    already set one. File persistence stays available as an explicit opt-in.
    Tenant mode (the image default) scopes owners per request, so no owner id
    is wired here."""
    derived=dict(env)
    if not derived.get(SESSIONS_DSN_ENV):
        db=derived.get('DELIVERY_DATABASE_URL','')
        if db.startswith(('postgresql://','postgres://')):
            derived[SESSIONS_DSN_ENV]=db
    return derived


def check_env(env):
    problems=[]
    token=env.get('INTERVIEW_ENGINE_TOKEN','')
    if not token or token==DEFAULT_TOKEN or len(token)<24:
        problems.append('INTERVIEW_ENGINE_TOKEN must be set to a private value of at least 24 characters (not the local default)')
    if env.get('DELIVERY_CONTROL_PLANE_ONLY','0')!='0':
        problems.append('DELIVERY_CONTROL_PLANE_ONLY must be 0 in the full service')
    resolved=engine_env(env)
    if not resolved.get(SESSIONS_DSN_ENV) and resolved.get(SESSIONS_FILE_ENV)!='1':
        problems.append('engine session persistence is not configured: set '+SESSIONS_DSN_ENV+' (a PostgreSQL DELIVERY_DATABASE_URL is used automatically) or explicitly choose file persistence with '+SESSIONS_FILE_ENV+'=1')
    return problems


def commands(python=None):
    python=python or sys.executable
    engine=[python,'-m','uvicorn','interview_engine.http_service:app','--host',ENGINE_HOST,'--port',str(ENGINE_PORT)]
    delivery=[python,'-m','delivery_api']
    return engine,delivery


def port_open(host,port,timeout=0.5):
    try:
        with socket.create_connection((host,port),timeout=timeout):return True
    except OSError:return False


def healthcheck(env=os.environ):
    port=int(env.get('DELIVERY_PORT','8000'))
    with urllib.request.urlopen(f'http://127.0.0.1:{port}/health',timeout=3) as response:
        body=json.load(response)
    assert body.get('ok') is True and body.get('runtime_profile')!='control_plane_only'
    assert port_open(ENGINE_HOST,int(env.get('INTERVIEW_ENGINE_PORT',str(ENGINE_PORT))))


def main():
    if '--healthcheck' in sys.argv:
        try:healthcheck()
        except Exception as error:
            print('unhealthy:',error,file=sys.stderr);return 1
        return 0
    problems=check_env(os.environ)
    if problems:
        for p in problems:print('full service not started:',p,file=sys.stderr)
        return 2
    engine_cmd,delivery_cmd=commands()
    engine=subprocess.Popen(engine_cmd,env=engine_env(os.environ))
    procs=[engine]
    def stop(*_):
        for p in procs:
            if p.poll() is None:p.terminate()
    signal.signal(signal.SIGTERM,stop);signal.signal(signal.SIGINT,stop)
    for _ in range(120):
        if engine.poll() is not None:
            print('interview engine exited during start',file=sys.stderr);return 3
        if port_open(ENGINE_HOST,ENGINE_PORT):break
        time.sleep(0.5)
    else:
        stop();print('interview engine did not open its port in 60s',file=sys.stderr);return 3
    delivery=subprocess.Popen(delivery_cmd);procs.append(delivery)
    while True:
        for p in procs:
            code=p.poll()
            if code is not None:
                stop()
                for q in procs:
                    try:q.wait(timeout=20)
                    except subprocess.TimeoutExpired:q.kill()
                print(f'{"engine" if p is engine else "delivery"} exited with {code}; stopping the container',file=sys.stderr)
                return code or 1
        time.sleep(1)


if __name__=='__main__':sys.exit(main())
