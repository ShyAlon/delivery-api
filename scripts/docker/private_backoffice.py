"""Own one private host Back office listener for the accepted preview lifetime."""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import urllib.request
from image_workflow import WorkflowError


# A previous back office needs a moment to release its port after shutdown; a
# retry spans that window instead of failing the preview on a dying listener.
BIND_WAIT_SECONDS=10


class PrivateBackoffice:
    def __init__(self,port,upstream_port,log_path,bind_wait_seconds=BIND_WAIT_SECONDS):
        if type(port) is not int or not 1024<=port<=65535 or port in (upstream_port,8000,8010):
            raise WorkflowError('Back office private port conflicts or is invalid')
        if type(bind_wait_seconds) not in (int,float) or not 0<=bind_wait_seconds<=60:
            raise WorkflowError('Back office bind wait must be a number of seconds from 0 to 60')
        self.port=port;self.upstream_port=upstream_port;self.log_path=Path(log_path)
        self.bind_wait_seconds=bind_wait_seconds;self.process=None;self.log=None

    def _own_port(self):
        deadline=time.monotonic()+self.bind_wait_seconds
        while True:
            try:
                with socket.socket() as listener:
                    # SO_REUSEADDR: a just-closed predecessor leaves connections
                    # cooling down on this port; they must not block the new owner.
                    listener.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
                    listener.bind(('127.0.0.1',self.port))
                return
            except OSError:
                if time.monotonic()>=deadline:break
                time.sleep(.25)
        raise WorkflowError('Back office cannot own its private port ('+self._holder()+'); existing listener untouched') from None

    def _holder(self):
        import shutil
        if shutil.which('lsof') is None:return 'port '+str(self.port)+' busy; lsof unavailable to name the holder'
        try:
            found=subprocess.run(['lsof','-nP','-iTCP:'+str(self.port),'-sTCP:LISTEN'],capture_output=True,text=True,timeout=5)
        except (OSError,subprocess.TimeoutExpired):return 'port '+str(self.port)+' busy; holder lookup failed'
        holders=[line for line in found.stdout.splitlines() if line.strip()]
        if found.returncode!=0 or len(holders)<2:
            return 'port '+str(self.port)+' has no current listener; a recently closed one may still be cooling down'
        return 'port '+str(self.port)+' is held by: '+' | '.join(holders[1:4])

    def start(self):
        self._own_port()
        self.log_path.parent.mkdir(parents=True,exist_ok=True)
        fd=os.open(self.log_path,os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600)
        self.log=os.fdopen(fd,'w')
        # No database/provider settings are needed in the separate API proxy.
        env={key:os.environ[key] for key in ('PATH','LANG','HOME','PYTHONPATH') if key in os.environ}
        env.update(BACKOFFICE_HOST='127.0.0.1',BACKOFFICE_PORT=str(self.port),
                   BACKOFFICE_SERVICE_URL='http://127.0.0.1:'+str(self.upstream_port))
        try:
            self.process=subprocess.Popen([sys.executable,'-m','delivery_api.backoffice'],env=env,stdout=self.log,stderr=self.log)
            for _ in range(100):
                if self.process.poll() is not None:break
                try:
                    with urllib.request.urlopen('http://127.0.0.1:'+str(self.port)+'/health',timeout=.5) as response:
                        value=json.load(response)
                    time.sleep(.1)
                    if value.get('ok') is True and value.get('role')=='backoffice' and self.process.poll() is None:return self
                except (OSError,ValueError):pass
                time.sleep(.1)
            raise WorkflowError('Back office did not become healthy; owned process stopped, inspect private log')
        except BaseException:
            self.close();raise

    def close(self):
        if self.process is not None:
            if self.process.poll() is None:
                self.process.terminate()
                try:self.process.wait(timeout=10)
                except subprocess.TimeoutExpired:self.process.kill();self.process.wait(timeout=5)
            self.process=None
        if self.log is not None:self.log.close();self.log=None
