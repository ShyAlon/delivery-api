"""Diagnostic-only child fixture: real subprocess stream behind a scoped registry.

No provider, publication import or authority bypass. The application runner still
checks the real owner/session and consumes only an explicitly registered process.
Actual production child protocol is covered in test_child_generator_cutover.
"""
import asyncio,os,uuid
from pathlib import Path
import pytest

@pytest.fixture
def registered_runner(monkeypatch,tmp_path):
    from delivery_api.runner import ProcessRunner
    from delivery_api import inprocess_engine
    from delivery_api.tenancy import session_root,current_scope
    original=ProcessRunner.run
    async def run(self,task_id,command,*,cwd,env=None,**options):
        if self.store.engine_session(task_id) is None:
            self.store.set_engine_session(task_id,'runner-fixture-'+uuid.uuid4().hex)
        from delivery_api import config
        monkeypatch.setattr(config,'CLOUD_TENANTS',True)
        base=Path(os.environ.get('VMODEL_ARTIFACTS_DIR',str(tmp_path/'artifacts')))
        root=session_root(base,current_scope(),self.store.engine_session(task_id))
        monkeypatch.setenv('VMODEL_ARTIFACTS_DIR',str(base))
        proc=inprocess_engine.VirtualProcess(asyncio.get_running_loop())
        inprocess_engine._registry[task_id]=proc
        async def produce():
            proc.started.set()
            child=await asyncio.create_subprocess_exec(*command,cwd=cwd,env={**os.environ,**(env or {})},
                stdin=asyncio.subprocess.PIPE,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.STDOUT)
            async def stream():
                while raw:=await child.stdout.read(512):proc.feed(raw)
            pump=asyncio.create_task(stream())
            async def stop():
                while not proc.stop_requested and child.returncode is None:await asyncio.sleep(.01)
                if proc.stop_requested and child.returncode is None:child.terminate()
            watcher=asyncio.create_task(stop())
            try:
                rc=await child.wait();await pump;proc.finish(rc)
            finally:
                watcher.cancel()
                if child.returncode is None:child.kill();await child.wait()
        work=asyncio.create_task(produce())
        try:
            return await original(self,task_id,['fixture-child','--out',str(root/'spec')],cwd=cwd,
                env={**(env or {}),'VMODEL_ARTIFACTS_DIR':str(root.parent)},**options)
        finally:
            proc.terminate();await work
            inprocess_engine._registry.pop(task_id,None)
    monkeypatch.setattr(ProcessRunner,'run',run)
