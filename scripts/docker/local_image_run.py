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
    for relative in ('scripts/docker/fixture_ingress.py','scripts/docker/Dockerfile.local-fixture','scripts/docker/local_service_fixture.py','scripts/docker/fixture_services.py','scripts/docker/retained_image_proof.py'):
        target=destination/relative;target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(root/relative,target)
    # Public-only context: copied paths must be readable by fixture UID10001,
    # independently of the owner's private umask. Never add settings or keys.
    for path in destination.rglob('*'):path.chmod(0o755 if path.is_dir() else 0o644)


class OwnedRun:
    def __init__(self, root: Path, image_id: str):
        self.root=root;self.image_id=image_id;self.suffix=uuid.uuid4().hex[:24]
        self.network='requi-fixture-'+self.suffix;self.volume=self.network
        self.fixture='requi-fixture-services-'+self.suffix
        self.service='requi-fixture-service-'+self.suffix
        self.front=self.network+'-ingress'
        self.ingress='requi-fixture-ingress-'+self.suffix
        self.label='requi.image-gate='+self.suffix
        self.created=[]
        self.fixture_built=False
        self.fixture_tag='requi-local-fixture:'+self.suffix
        self.directory=None
        self.diagnostic_path=None

    def scrub(self,text):
        import re
        text=str(text)
        for value in getattr(self,'handoff',{}).values():
            if isinstance(value,str) and value:text=text.replace(value,'[fixture value redacted]')
        text=re.sub(r'(?i)(?:postgres(?:ql)?://)[^\s\"\']+','[database URL redacted]',text)
        text=re.sub(r'(?i)(authorization|password|token|api[_-]?key)([\"\']?\s*[:=]\s*[\"\']?)[^\s,\"\']+',r'\1\2[redacted]',text)
        text=re.sub(r'eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+','[JWT redacted]',text)
        return text[-24000:]

    def check_container(self,name):
        state=object_json(command(['docker','container','inspect','--format','{{json .State}}',name],timeout=3).encode())
        if state.get('Running') is not True or state.get('Health',{}).get('Status')=='unhealthy':
            raise WorkflowError('owned container '+name+' '+str(state.get('Status'))+
                ' exit='+str(state.get('ExitCode'))+' oom='+str(state.get('OOMKilled'))+
                ' error='+self.scrub(state.get('Error','')))
        return state

    def diagnose(self,error):
        # Only invocation-owned fictional fixtures. Never inspect Config.Env or
        # persist the handoff/settings/token. Capture before destructive cleanup.
        chunks=['exact-image local acceptance failed: '+type(error).__name__+': '+self.scrub(error)]
        for kind,name in self.created:
            if kind!='container':continue
            try:
                labels=object_json(command(['docker','container','inspect','--format','{{json .Config.Labels}}',name]).encode())
                if labels.get('requi.image-gate')!=self.suffix:
                    chunks.append(name+': ownership differs, diagnostic read refused');continue
                state=command(['docker','container','inspect','--format','{{json .State}}',name])
                chunks.append(name+' state: '+self.scrub(state))
                tail=command(['docker','logs','--tail','100',name],include_stderr=True)
                chunks.append(name+' log tail:\n'+self.scrub(tail))
                if name==self.service and object_json(state.encode()).get('Running') is True:
                    probe=subprocess.run(['docker','exec',name,'python','/app/full_entry.py','--healthcheck'],capture_output=True,text=True,timeout=5)
                    chunks.append('in-container healthcheck exit='+str(probe.returncode)+'\n'+self.scrub(probe.stdout+probe.stderr))
            except Exception as diagnostic_error:
                chunks.append(name+': diagnostics unavailable: '+type(diagnostic_error).__name__)
        if hasattr(self,'base'):
            chunks.append('host probe URL: '+self.base+'/health')
            try:
                chunks.append('docker port ingress: '+command(['docker','port',self.ingress,'8000/tcp'],timeout=3))
                chunks.append('actual ingress bindings: '+command(['docker','container','inspect','--format','{{json .NetworkSettings.Ports}}',self.ingress],timeout=3))
            except WorkflowError:chunks.append('docker port ingress unavailable')
        text='\n'.join(chunks)+'\n'
        path=getattr(self,'diagnostic_path',None)
        if path is not None:
            try:
                from image_receipt import atomic_write
                atomic_write(path,text.encode())
                log('FAIL','retained startup/acceptance diagnostics: '+str(path))
            except OSError:log('WARN','could not retain diagnostic file; terminal evidence follows')
        log('FAIL',text)

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
        command(['docker','build','--progress','plain','--platform','linux/amd64','--file',str(build_context/'scripts/docker/Dockerfile.local-fixture'),
                 '--tag',self.fixture_tag,str(build_context)],timeout=1800,output_path=self.diagnostic_path.with_name('fixture-build.log') if self.diagnostic_path else None)
        self.fixture_built=True
        self.create('network',self.network,['docker','network','create','--internal','--label',self.label,self.network])
        self.create('volume',self.volume,['docker','volume','create','--label',self.label,self.volume])
        self.create('container',self.fixture,['docker','run','--detach','--name',self.fixture,'--label',self.label,
            '--platform','linux/amd64','--read-only','--cap-drop','ALL','--security-opt','no-new-privileges',
            '--sysctl','net.ipv4.ip_unprivileged_port_start=0','--network',self.network,'--network-alias','image-fixture.supabase.co',
            '--mount','type=volume,source='+self.volume+',target=/fixture',
            '--tmpfs','/tmp:rw,nosuid,nodev,size=512m,mode=1777',self.fixture_tag])
        handoff=None
        fixture_deadline=time.monotonic()+120
        while time.monotonic()<fixture_deadline:
            self.check_container(self.fixture)
            try:
                handoff=object_json(command(['docker','exec',self.fixture,'cat','/tmp/fixture-state.json'],timeout=3).encode());break
            except (WorkflowError,ValueError):time.sleep(.5)
        self.handoff=handoff or {}
        if handoff is None:raise WorkflowError('owned local fixture readiness timed out')
        if set(handoff)!={'dsn','token','issuer'} or not handoff['dsn'].startswith('postgresql:///test_tenant_') or handoff['issuer']!='https://image-fixture.supabase.co/auth/v1':
            raise WorkflowError('owned fixture handoff identity differs')
        with socket.socket() as listener:
            listener.bind(('127.0.0.1',0));port=listener.getsockname()[1]
        target=Target(self.image_id,self.service,str(scratch/'service.env'),port,self.network,self.volume,publish=False)
        self.base=target.origin
        env={'DELIVERY_DATABASE_URL':handoff['dsn'],'DELIVERY_SUPABASE_URL':'https://image-fixture.supabase.co',
             'DELIVERY_SUPABASE_ANON_KEY':'fictional-public-key','DELIVERY_PUBLIC_ORIGIN':self.base,
             'INTERVIEW_ENGINE_TOKEN':'owned-image-gate-token-'+self.suffix,'REQUI_PIN_PROVIDERS':'ollama',
             'OLLAMA_BASE_URL':'http://image-fixture.supabase.co:11434/v1','DELIVERY_OLLAMA_MODEL':'phi4',
             'SSL_CERT_FILE':'/fixture/ca.pem','REQUESTS_CA_BUNDLE':'/fixture/ca.pem'}
        settings=scratch/'service.env';settings.write_text(''.join(k+'='+v+'\n' for k,v in env.items()));settings.chmod(0o600)
        args=target.arguments()
        # Ownership is host-side metadata, not an execution mode.
        args[1:1]=['--label',self.label]
        self.create('container',self.service,['docker',*args])
        self.handoff=handoff
        # Only the relay has a gateway network. The unchanged candidate and
        # fixture retain internal-only outbound isolation on every Docker host.
        self.create('network',self.front,['docker','network','create','--label',self.label,self.front])
        self.create('container',self.ingress,['docker','run','--detach','--name',self.ingress,'--label',self.label,
            '--platform','linux/amd64','--read-only','--cap-drop','ALL','--security-opt','no-new-privileges',
            '--pids-limit','64','--memory','64m','--network',self.front,'--publish',target.binding,
            self.fixture_tag,'python','/harness/scripts/docker/fixture_ingress.py',self.service])
        command(['docker','network','connect',self.network,self.ingress],timeout=5)
        self.wait_host(target)
        return self

    def wait_host(self,target,*,seconds=90):
        import urllib.request
        # Never send fixture requests through a Mac's configured HTTP proxy.
        opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
        deadline=time.monotonic()+seconds
        last='no response'
        while time.monotonic()<deadline:
            try:
                with opener.open(target.origin+'/health',timeout=min(1,max(.01,deadline-time.monotonic()))) as response:
                    if json.load(response).get('ok') is True:return
            except (OSError,ValueError) as error:last=type(error).__name__+': '+self.scrub(error)
            state=self.check_container(self.service)
            if time.monotonic()>=deadline:break
            self.check_container(self.ingress)
            mapping=command(['docker','port',self.ingress,'8000/tcp'],timeout=min(3,max(.01,deadline-time.monotonic())))
            expected=target.binding.rsplit(':',1)[0]
            if mapping!=expected:
                raise WorkflowError('host publication missing/different: probe='+target.origin+'/health; docker port='+mapping+'; expected='+expected)
            if state.get('Health',{}).get('Status')=='healthy':
                raise WorkflowError('container healthy but host ingress failed: probe='+target.origin+'/health; docker port='+mapping+'; '+last)
            time.sleep(min(.5,max(0,deadline-time.monotonic())))
        raise WorkflowError('host readiness deadline exceeded: probe='+target.origin+'/health; '+last)

    def active_stop(self):
        from image_acceptance import API
        api=API(self.base,self.handoff['token'])
        task=api('POST','/api/tasks',{'title':'Owned active Stop fixture','brief':'EXACT_IMAGE_STOP: A simple web booking app for one tutor.'})
        tid=task['task_id']
        api('POST','/api/tasks/'+tid+'/start',{'model_route':'ollama'})
        active=False
        for _ in range(120):
            health=object_json(command(['docker','exec',self.fixture,'python','-c',
                "import urllib.request;print(urllib.request.urlopen('http://127.0.0.1:11434/health',timeout=2).read().decode())"]).encode())
            if health.get('active') is True:active=True;break
            time.sleep(.1)
        if not active:raise WorkflowError('Stop fixture never reached an active real provider call')
        # One Stop request only, never repeat a mutation to manufacture success.
        api('POST','/api/tasks/'+tid+'/stop')
        for _ in range(120):
            stopped=api('GET','/api/tasks/'+tid)
            if stopped.get('state')=='stopped':break
            time.sleep(.25)
        else:raise WorkflowError('active-call Stop never reached confirmed stopped state')
        return {'task_id':tid,'active-call-observed':True,'stop':'passed'}

    def http_acceptance(self, output: Path):
        # One repo entry, same input identity/answer protocol as owner acceptance.
        result=subprocess.run(['bash',str(self.root/'scripts/ops/acceptance.sh'),'--image-local','--base-url',self.base,'--output',str(output)],
            input=self.handoff['token'],text=True,capture_output=True,cwd=self.root,timeout=600)
        if result.returncode:
            raise WorkflowError('unchanged-image signed HTTP acceptance failed; exit='+str(result.returncode)+'; no receipt issued\n'+self.scrub(result.stdout+result.stderr))
        actual=command(['docker','container','inspect','--format','{{.Image}}',self.service])
        if actual!=self.image_id:raise WorkflowError('running image changed during acceptance')
        result=object_json(output.read_bytes())
        proof=object_json(command(['docker','exec',self.fixture,'python','/harness/scripts/docker/retained_image_proof.py',result['task_id']]).encode())
        if proof.get('task_id')!=result['task_id'] or proof.get('child-lifecycle')!='passed' or proof.get('retained-outputs')!='passed':
            raise WorkflowError('owned retained child/output proof did not pass')
        result['retained']=proof
        result['active-stop']=self.active_stop()
        return result
