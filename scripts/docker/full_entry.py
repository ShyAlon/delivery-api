"""Full-service launcher: the interview engine (loopback only) and the delivery API in one container.

Either process exiting stops the other and fails the container, so the restart policy sees it.
`--healthcheck` is the container health probe. No provider is called by this file."""
import json,os,signal,socket,subprocess,sys,threading,time,urllib.request

sys.path.insert(0,os.path.dirname(os.path.abspath(__file__)))   # preflight.py sits next to this file (/app in the image)
import preflight
from datetime import datetime,timezone
from pathlib import Path

ENGINE_HOST='127.0.0.1'
ENGINE_PORT=8010
DEFAULT_TOKEN='local-interview-engine-token'


SESSIONS_DSN_ENV='INTERVIEW_ENGINE_SESSIONS_DSN'
SESSIONS_FILE_ENV='INTERVIEW_ENGINE_SESSIONS_FILE'

ENGINE_LOG_MAX_BYTES=2*1024*1024


def engine_log_path(env):
    """Where the engine's copied output lands. Mirrors delivery_api.config.default_data_dir
    (XDG_DATA_HOME or ~/.local/share, plus requi-re) so the delivery process reads back the
    same file for the Debug server log."""
    base=env.get('XDG_DATA_HOME')
    root=(Path(base).expanduser() if base else Path.home()/'.local'/'share')/'requi-re'
    return root/'logs'/'engine.log'


def tee_engine_output(engine,path,max_bytes=ENGINE_LOG_MAX_BYTES,out=None):
    """Copy the engine's combined stdout/stderr to our own stdout (the container log stays the
    live view, bytes unchanged) and to a bounded file the delivery API reads back for the Debug
    server log. File lines carry a UTC timestamp so the read side can window them; the file is
    rotated once at max_bytes. Never fatal: without a writable file the output still passes through."""
    out=out or sys.stdout
    sink=None
    try:
        path.parent.mkdir(parents=True,exist_ok=True)
        if path.exists() and path.stat().st_size>max_bytes:os.replace(path,path.with_suffix('.log.1'))
        sink=open(path,'a',encoding='utf-8',buffering=1)
    except OSError:sink=None
    def pump():
        nonlocal sink
        for line in engine.stdout:
            out.write(line);out.flush()
            if sink:
                try:
                    if sink.tell()>max_bytes:
                        sink.close();os.replace(path,path.with_suffix('.log.1'))
                        sink=open(path,'a',encoding='utf-8',buffering=1)
                    stamp=datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
                    sink.write(stamp+' '+line)
                except OSError:
                    try:sink.close()
                    except OSError:pass
                    sink=None
        if sink:
            try:sink.close()
            except OSError:pass
    threading.Thread(target=pump,daemon=True).start()


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


def artifacts_root_problem(env):
    """Preflight check 1, kept under its public name: probe the artifacts root (mkdir -p + a real
    write) and return the problem text, or None when ready."""
    return preflight.artifacts_root_problem(env)


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
    report=preflight.run(os.environ)
    print(report.render_text(),flush=True)
    preflight.write_report(report,preflight.report_path(os.environ))
    if report.refused:
        print('full service not started: the startup preflight found blocking problems (above)',file=sys.stderr)
        return 2
    engine_cmd,delivery_cmd=commands()
    engine=subprocess.Popen(engine_cmd,env=engine_env(os.environ),
                            stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,errors='replace',bufsize=1)
    tee_engine_output(engine,engine_log_path(os.environ))
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
