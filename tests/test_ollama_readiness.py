"""Read-only diagnostics never load models or mistake file size for fit."""
import sys,json,io
from pathlib import Path
from types import SimpleNamespace
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts/docker'))
from delivery_api.ollama_readiness import probe
from delivery_api import ollama_readiness as reader
import ollama_readiness as m
from test_local_pg_preview import source
from local_pg_preview import settings,names,WorkflowError


def test_explicit_ollama_values_cross_without_paid_fallback(tmp_path):
 value=settings(source(tmp_path),'a'*32,names('a'*32),8002,{'REQUI_PIN_PROVIDERS':'ollama','DELIVERY_OLLAMA_MODEL':'tiny:latest'})
 assert value['REQUI_PIN_PROVIDERS']=='ollama'
 assert value['DELIVERY_OLLAMA_MODEL']=='tiny:latest'
 assert value['OLLAMA_BASE_URL']=='http://host.docker.internal:11434/v1'
 assert 'DELIVERY_LOCAL_PREVIEW' not in value


def test_no_implicit_model_or_container_loopback(tmp_path):
 # Model choice belongs to the UI registry, not launch flags.
 value=settings(source(tmp_path,REQUI_PIN_PROVIDERS='ollama'),'a'*32,names('a'*32),8002)
 assert not value.get('DELIVERY_OLLAMA_MODEL')
 assert value['OLLAMA_BASE_URL']=='http://host.docker.internal:11434/v1'
 with pytest.raises(WorkflowError,match='container loopback'):
  settings(source(tmp_path,REQUI_PIN_PROVIDERS='ollama',DELIVERY_OLLAMA_MODEL='phi4',OLLAMA_BASE_URL='http://localhost:11434/v1'),'a'*32,names('a'*32),8002)


def test_readonly_tags_and_loaded_sizes_not_fit(monkeypatch):
 requests=[]
 class Response(io.BytesIO):
  def __enter__(self):return self
  def __exit__(self,*a):self.close()
 class Opener:
  def open(self,request,timeout):
   requests.append((request.method,request.full_url))
   data={'models':[{'name':'small:latest','size':1000,'size_vram':700,'context_length':4096}]} if request.full_url.endswith('/api/ps') else {'models':[{'name':'small:latest','size':999}]}
   return Response(json.dumps(data).encode())
 monkeypatch.setattr(reader,'build_opener',lambda *a:Opener())
 report=probe('http://host.docker.internal:11434/v1','small')
 assert requests==[('GET','http://host.docker.internal:11434/api/tags'),('GET','http://host.docker.internal:11434/api/ps')]
 assert report['reachable'] and report['selected_model_present']
 assert report['installed_models']==[{'id':'small:latest','file_bytes':999}]
 assert report['memory_fit']=='unverified' and report['loaded_models'][0]['resident_bytes']==1000


def test_private_endpoint_not_contacted_or_printed(monkeypatch):
 monkeypatch.setattr(reader,'build_opener',lambda *a:(_ for _ in ()).throw(AssertionError('must not connect')))
 report=probe('http://user:private@localhost:11434/v1')
 assert report['reason']=='endpoint_invalid' and 'private' not in str(report)


def test_checker_is_early_readonly_goal_without_acceptance_or_git_preflight(monkeypatch,tmp_path,capsys):
 import image_workflow as workflow
 monkeypatch.setattr(workflow,'preflight',lambda *a:(_ for _ in ()).throw(AssertionError('must not run')))
 monkeypatch.setattr(m,'diagnose',lambda *a,**kw:{'read_only':True})
 assert workflow.main(['--local-pg','check-ollama'])==0
 assert json.loads(capsys.readouterr().out)['read_only']


def test_owned_container_check_uses_running_settings_without_writes(monkeypatch,tmp_path):
 import local_pg_preview as preview
 home=tmp_path/'private';home.mkdir();identity=home/'identity.json';identity.write_text(json.dumps({'nonce':'a'*32}));identity.chmod(0o600)
 monkeypatch.setattr(preview,'inspect_owned',lambda *a:{'State':{'Running':True}})
 monkeypatch.setattr(m,'probe',lambda *a:{'reachable':True,'installed_models':[{'id':'small:latest'}]})
 calls=[]
 def run(args,**kw):
  calls.append(args)
  if args[0]=='sysctl':return SimpleNamespace(returncode=0,stdout=str(8*1024**3))
  assert args[:3]==['docker','exec','--interactive']
  assert args[-2:]==['',''], 'diagnostic must use actual running app values unless override explicitly supplied'
  assert 'os.environ.get' in kw['input'] and 'probe(base,model)' in kw['input']
  return SimpleNamespace(returncode=0,stdout=json.dumps({'reachable':False,'model_selected':False,'ollama_provider_selected':False,'endpoint_topology':'container_loopback'}))
 monkeypatch.setattr(m.subprocess,'run',run)
 report=m.diagnose(source(tmp_path),home)
 assert len(calls)==2 and len(report['missing'])==2
 assert 'No launch-model setting or operator pin is required' in report['approved_pin_note']
 assert report['mac_physical_ram_bytes']==8*1024**3 and report['memory_fit']=='unverified'
 assert 'production.invalid' not in str(report) and 'must-not-cross' not in str(report)
 assert identity.exists()


@pytest.mark.parametrize('version,ready',[('0.17.1',True),('0.18.0',True),('0.16.0',False),('unknown',False)])
def test_native_models_require_verified_no_truncation_controls(monkeypatch,version,ready):
 class Response(io.BytesIO):
  def __enter__(self):return self
  def __exit__(self,*a):self.close()
 class Opener:
  def open(self,request,timeout):
   if request.full_url.endswith('/api/version'):data={'version':version}
   elif request.full_url.endswith('/api/show'):data={'capabilities':['completion','thinking'],'thinking':{'values':[True,False]},'model_info':{'general.architecture':'qwen','qwen.context_length':32768}}
   else:data={'models':[{'name':'qwen3:8b','digest':'500a1f067a9f'+'a'*52}]}
   return Response(json.dumps(data).encode())
 monkeypatch.setattr(reader,'build_opener',lambda *a:Opener())
 report=probe('http://localhost:11434/v1','qwen3:8b')
 assert report['reachable']
 assert ('native_context' in report['installed_models'][0]) is ready
