"""One delivery parent supervises per-job children on every target.

Delivery exiting fails the container, so the restart policy sees it.
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
    from delivery_api.process_logs import emit
    import re
    def pump_pg():
        try:
            for line in engine.stdout:
                out.write(line);out.flush()
                found=re.search(r'\bsession=([0-9a-fA-F-]{36})\b',line)
                emit('engine',line.rstrip('\n'),session_id=found.group(1) if found else None)
        finally:engine.stdout.close()
    threading.Thread(target=pump_pg,daemon=True).start()
    return


def engine_env(env):
    """Derive the required PG DSN, never accept a file-session escape hatch."""
    derived=dict(env)
    if not derived.get(SESSIONS_DSN_ENV):
        db=derived.get('DELIVERY_DATABASE_URL','')
        if db.startswith(('postgresql://','postgres://')):
            derived[SESSIONS_DSN_ENV]=db
    derived[SESSIONS_FILE_ENV]='0'
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
    resolved=engine_env(env)
    if not resolved.get(SESSIONS_DSN_ENV,'').startswith(('postgresql://','postgres://')):
        problems.append('PostgreSQL engine session persistence is required: set '+SESSIONS_DSN_ENV+' or DELIVERY_DATABASE_URL; retired '+SESSIONS_FILE_ENV+' cannot bypass this')
    return problems


def command(python=None):
    return [python or sys.executable,'-m','delivery_api']


def healthcheck(env=os.environ):
    port=int(env.get('DELIVERY_PORT','8000'))
    with urllib.request.urlopen(f'http://127.0.0.1:{port}/health',timeout=3) as response:
        body=json.load(response)
    assert body.get('ok') is True


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
    saved=preflight.write_report(report,preflight.report_path(os.environ))
    os.environ['REQUI_PREFLIGHT_GENERATED_AT']=report.generated_at
    os.environ['REQUI_PREFLIGHT_REPORT_SAVED']='1' if saved else '0'
    if report.refused:
        print('full service not started: the startup preflight found blocking problems (above)',file=sys.stderr)
        return 2
    procs=[]
    def stop(*_):
        for p in procs:
            if p.poll() is None:p.terminate()
    signal.signal(signal.SIGTERM,stop);signal.signal(signal.SIGINT,stop)
    delivery=subprocess.Popen(command());procs.append(delivery)
    while True:
        for p in procs:
            code=p.poll()
            if code is not None:
                stop()
                for q in procs:
                    try:q.wait(timeout=20)
                    except subprocess.TimeoutExpired:q.kill()
                print(f'delivery exited with {code}; stopping the container',file=sys.stderr)
                return code or 1
        time.sleep(1)


if __name__=='__main__':sys.exit(main())
