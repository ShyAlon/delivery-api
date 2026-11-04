"""Owned Docker resources for the unchanged-image local acceptance gate."""
from __future__ import annotations
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
import time
import uuid

from image_receipt import object_json
from runtime_spec import Target
from image_workflow import WorkflowError, command, inspect_image, log


def context(root: Path, destination: Path):
    import importlib.util
    spec=importlib.util.spec_from_file_location('minimal_smoke_context',root/'scripts/docker/smoke-context.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    module.prepare(root,destination)
    for relative in ('scripts/docker/Dockerfile.local-fixture','scripts/docker/local_service_fixture.py','scripts/docker/fixture_services.py','scripts/docker/retained_image_proof.py'):
        target=destination/relative;target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(root/relative,target)


class OwnedRun:
    def __init__(self, root: Path, image_id: str):
        self.root=root;self.image_id=image_id;self.suffix=uuid.uuid4().hex[:24]
        self.network='requi-fixture-'+self.suffix;self.volume=self.network
        self.fixture='requi-fixture-services-'+self.suffix
        self.service='requi-fixture-service-'+self.suffix
        self.label='requi.image-gate='+self.suffix
        self.created=[]
        self.fixture_built=False
        self.fixture_tag='requi-local-fixture:'+self.suffix
        self.directory=None

    def create(self,kind,name,args):
        command(args)
        self.created.append((kind,name))

    def close(self):
        # Verify the unique ownership label before removing anything, even if a
        # third party replaced a resource during this invocation.
        for kind,name in reversed(self.created):
            inspect_kind='container' if kind=='container' else kind
            try:
                raw=command(['docker',inspect_kind,'inspect','--format','{{json .Labels}}' if kind!='container' else '{{json .Config.Labels}}',name])
                labels=object_json(raw.encode())
                if labels.get('requi.image-gate')!=self.suffix:
                    log('WARN','owned cleanup identity differs; resource retained for inspection');continue
                remove=['docker','rm','--force',name] if kind=='container' else ['docker',kind,'rm',name]
                command(remove)
            except (WorkflowError,ValueError):log('WARN','owned cleanup unconfirmed; inspect invocation resources')
        self.created=[]
        if self.fixture_built:
            try:command(['docker','image','rm',self.fixture_tag])
            except WorkflowError:pass
        if self.directory is not None:self.directory.cleanup()

    def start(self):
        image=inspect_image(self.image_id)
        if image['Id']!=self.image_id:raise WorkflowError('candidate image identity changed')
        self.directory=tempfile.TemporaryDirectory(prefix='requi-image-gate-')
        scratch=Path(self.directory.name);build_context=scratch/'context';build_context.mkdir()
        context(self.root,build_context)
        command(['docker','build','--platform','linux/amd64','--file',str(build_context/'scripts/docker/Dockerfile.local-fixture'),
                 '--tag',self.fixture_tag,str(build_context)],timeout=1800)
        self.fixture_built=True
        self.create('network',self.network,['docker','network','create','--internal','--label',self.label,self.network])
        self.create('volume',self.volume,['docker','volume','create','--label',self.label,self.volume])
        self.create('container',self.fixture,['docker','run','--detach','--name',self.fixture,'--label',self.label,
            '--platform','linux/amd64','--read-only','--cap-drop','ALL','--security-opt','no-new-privileges',
            '--network',self.network,'--network-alias','image-fixture.supabase.co',
            '--mount','type=volume,source='+self.volume+',target=/fixture',
            '--tmpfs','/tmp:rw,nosuid,nodev,size=512m,mode=1777',self.fixture_tag])
        handoff=None
        for _ in range(240):
            try:
                handoff=object_json(command(['docker','exec',self.fixture,'cat','/tmp/fixture-state.json']).encode());break
            except (WorkflowError,ValueError):time.sleep(.5)
        if handoff is None:raise WorkflowError('owned local fixture readiness timed out')
        if set(handoff)!={'dsn','token','issuer'} or not handoff['dsn'].startswith('postgresql:///test_tenant_') or handoff['issuer']!='https://image-fixture.supabase.co/auth/v1':
            raise WorkflowError('owned fixture handoff identity differs')
        with socket.socket() as listener:
            listener.bind(('127.0.0.1',0));port=listener.getsockname()[1]
        self.base='http://127.0.0.1:'+str(port)
        env={'DELIVERY_DATABASE_URL':handoff['dsn'],'DELIVERY_SUPABASE_URL':'https://image-fixture.supabase.co',
             'DELIVERY_SUPABASE_ANON_KEY':'fictional-public-key','DELIVERY_PUBLIC_ORIGIN':self.base,
             'INTERVIEW_ENGINE_TOKEN':'owned-image-gate-token-'+self.suffix,'REQUI_PIN_PROVIDERS':'ollama',
             'OLLAMA_BASE_URL':'http://image-fixture.supabase.co:11434/v1','DELIVERY_OLLAMA_MODEL':'phi4',
             'SSL_CERT_FILE':'/fixture/ca.pem','REQUESTS_CA_BUNDLE':'/fixture/ca.pem'}
        settings=scratch/'service.env';settings.write_text(''.join(k+'='+v+'\n' for k,v in env.items()));settings.chmod(0o600)
        args=Target(self.image_id,self.service,str(settings),port,self.network,self.volume).arguments()
        # Ownership is host-side metadata, not an execution mode.
        args[1:1]=['--label',self.label]
        self.create('container',self.service,['docker',*args])
        self.handoff=handoff
        import urllib.request
        for _ in range(240):
            try:
                with urllib.request.urlopen(self.base+'/health',timeout=1) as response:
                    if json.load(response).get('ok') is True:break
            except (OSError,ValueError):pass
            time.sleep(.5)
        else:raise WorkflowError('unchanged candidate startup did not become healthy')
        return self

    def http_acceptance(self, output: Path):
        # One repo entry, same input identity/answer protocol as owner acceptance.
        result=subprocess.run(['bash',str(self.root/'scripts/ops/acceptance.sh'),'--image-local','--base-url',self.base,'--output',str(output)],
            input=self.handoff['token'],text=True,capture_output=True,cwd=self.root,timeout=600)
        if result.returncode:raise WorkflowError('unchanged-image signed HTTP acceptance failed; no receipt issued')
        actual=command(['docker','container','inspect','--format','{{.Image}}',self.service])
        if actual!=self.image_id:raise WorkflowError('running image changed during acceptance')
        result=object_json(output.read_bytes())
        proof=object_json(command(['docker','exec',self.fixture,'python','/harness/scripts/docker/retained_image_proof.py',result['task_id']]).encode())
        if proof.get('task_id')!=result['task_id'] or proof.get('child-lifecycle')!='passed' or proof.get('retained-outputs')!='passed':
            raise WorkflowError('owned retained child/output proof did not pass')
        result['retained']=proof
        return result
