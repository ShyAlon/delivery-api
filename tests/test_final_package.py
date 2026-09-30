import hashlib,json,zipfile
import pytest
from delivery_api.final_package import package,PackageBlocked

def handoff():
 files={name:'{}' for name in ('README.md','founder-decisions.json','founder-decisions.md','decisions.json','interview/intent_model.json','interview/conops.json','spec/needs.json','spec/srs.json','spec/architecture.json','spec/items.json','spec/tests.json','spec/AGENTS.md','spec/manifest.json','spec/traceability.json')}
 return {'session_id':'s','files':files,'sha256':{n:hashlib.sha256(x.encode()).hexdigest() for n,x in files.items()},'blocked_reasons':[]}

def task(generation=1):return {'task_id':'t','generation':generation,'state':'done','phases':[{'phase_key':'tests','state':'approved','completed_at':1}]}

def test_immutable_generation_packages_and_checksums(tmp_path):
 first=package(task(),'s',handoff(),tmp_path);again=package(task(),'s',handoff(),tmp_path)
 assert first['package_id']==again['package_id']
 next=package(task(2),'s',handoff(),tmp_path);assert next['package_id']!=first['package_id'];assert first['path'].is_file()
 with zipfile.ZipFile(first['path']) as z:
  manifest=json.loads(z.read('package.json'))
  assert manifest['generation']==1
  for name,value in manifest['sha256'].items():assert hashlib.sha256(z.read(name)).hexdigest()==value

def test_blocked_or_bad_source_never_exports(tmp_path):
 for change in ('open','state','run','log','traversal','checksum'):
  t=task();h=handoff();session='s'
  if change=='open':h['blocked_reasons']=['FD-style']
  if change=='state':t['state']='waiting_gate'
  if change=='run':session='other'
  if change in ('log','traversal'):
   name='spec/run.log' if change=='log' else '../private';h['files'][name]='private';h['sha256'][name]=hashlib.sha256(b'private').hexdigest()
  if change=='checksum':h['files']['README.md']='changed'
  with pytest.raises(PackageBlocked):package(t,session,h,tmp_path)
 assert not list(tmp_path.glob('*.zip'))
