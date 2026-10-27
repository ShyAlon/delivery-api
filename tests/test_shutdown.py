"""Real-process shutdown contract: open SSE and an engine child cannot pin uvicorn."""
import os
import pytest
import signal
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path


def _port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0));return sock.getsockname()[1]


@pytest.mark.parametrize('shutdown_signal',[signal.SIGINT,signal.SIGTERM],ids=['sigint','sigterm'])
def test_shutdown_closes_sse_and_terminates_engine_process(tmp_path,shutdown_signal):
    port=_port();pidfile=tmp_path/'engine.pid';root=Path(__file__).parents[1]
    engine=tmp_path/'engine.py'
    engine.write_text("import os,sys,time;open(sys.argv[1],'w').write(str(os.getpid()));time.sleep(120)\n")
    launcher=tmp_path/'server.py'
    launcher.write_text(f'''import asyncio,sys,uvicorn
from delivery_api.app import app,store,runner
TASK=store.create_task("Shutdown test","live child",task_id="shutdown-live")["task_id"]
from contextlib import asynccontextmanager
from delivery_api.app import lifespan as original_lifespan
@asynccontextmanager
async def test_lifespan(application):
    async with original_lifespan(application):
        job=asyncio.create_task(runner.run(TASK,[sys.executable,{str(engine)!r},{str(pidfile)!r}],cwd={str(tmp_path)!r}))
        yield
app.router.lifespan_context=test_lifespan
uvicorn.run(app,host="127.0.0.1",port={port},log_level="warning")
''')
    env={**os.environ,'PYTHONPATH':str(root/'src'),'DELIVERY_DATABASE_URL':f"sqlite:///{tmp_path/'shutdown.db'}"}
    server=subprocess.Popen([sys.executable,str(launcher)],env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    stream=None
    try:
        deadline=time.monotonic()+10
        while time.monotonic()<deadline and not pidfile.exists():
            if server.poll() is not None:raise AssertionError(server.stderr.read())
            time.sleep(.05)
        assert pidfile.exists()
        stream=subprocess.Popen([sys.executable,'-c',f"import time,urllib.request; r=urllib.request.urlopen('http://127.0.0.1:{port}/api/tasks/shutdown-live/stream');r.readline();time.sleep(120)"],env=env)
        time.sleep(.4);assert stream.poll() is None
        started=time.monotonic();server.send_signal(shutdown_signal)
        server.wait(timeout=7)
        assert time.monotonic()-started<7
        child=int(pidfile.read_text())
        try:os.kill(child,0)
        except ProcessLookupError:pass
        else:raise AssertionError(f'engine child {{child}} survived server shutdown')
        log=(server.stdout.read()+server.stderr.read())
        assert 'server.shutdown.signal' in log and "streams='1'" in log and 'engine.shutdown.signal' in log and 'server.shutdown.complete' in log and "streams_closed='1'" in log
    finally:
        if stream and stream.poll() is None:stream.kill();stream.wait()
        if server.poll() is None:server.kill();server.wait()
