"""Exact req rebuild --apply entry, isolated git origins and fake effect edges.

This proves command routing and gate ordering, not Docker acceptance or a live
migration. Every effect is a local test double and no remote service is used.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import textwrap
import pytest

ROOT=Path(__file__).resolve().parents[1]

HARNESS=r'''
import importlib.util,json,os,sys,datetime
from pathlib import Path
root=Path(sys.argv[2]);base=root.parent;home=Path(os.environ['HOME'])
spec=importlib.util.spec_from_file_location('rebuild',sys.argv[1]);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
log=Path(os.environ['COMMAND_LOG']);running=[False];image='sha256:'+'ab'*32
shas=m.origin_shas(base)
def record(kind,value):
    with log.open('a') as stream:stream.write(json.dumps([kind,value])+'\n')
def docker(args,**kw):
    record('docker',args)
    if args[:2]==['container','inspect']:
        if running[0]:return json.dumps({'Image':image,'State':{'Running':True}})
        raise m.WorkflowError('no test preview yet')
    if args[0]=='images' or (args[0]=='ps' and '-q' not in args):return '[]'
    if args[0]=='ps':return ''
    raise AssertionError(args)
def child(args,**kw):
    record('child',list(map(str,args)))
    if 'pull-all.sh' in str(args[1]):return
    tail=args[2:]
    if tail in (['status'],['image','--review']):return
    if tail==['image','--accept-local']:
        inputs={'sources':{n:shas[n] for n in m.IMAGE_REPOS},'platform':'linux/amd64','runtime_contract':'rt','acceptance_contract':'ac'}
        run=home/'.local/share/requi/image-workflow'/m.digest(json.dumps(inputs,sort_keys=True).encode()).split(':')[1][:24]
        run.mkdir(parents=True)
        (run/'state.json').write_text(json.dumps({'schema':1,'input_key':m.digest(json.dumps(inputs,sort_keys=True,separators=(',',':')).encode()),'stages':{
          'build':{'image_id':image,'sources':inputs['sources'],'platform':'linux/amd64'},
          'local-acceptance':{'image_id':image,'runtime_contract':'rt','checks':{n:'passed' for n in m.ACCEPTANCE_CHECKS}}}}))
        return
    if tail==['migrate','--apply']:return
    if tail==['preview','--apply']:running[0]=True;return
    raise AssertionError(args)
def probe(args):record('probe',list(map(str,args)));return int(os.environ['PENDING_CODE'])
def typed(prompt):record('prompt',prompt);return os.environ['GATE_ANSWER']
def context(root):
    record('main',sys.argv[2:])
    return {'root':root,'repos_root':base,'req':root/'scripts/ops/req','home':home,'shas':shas,'runtime_contract':'rt','acceptance_contract':'ac',
      'state_path':home/'rebuild.json','state':{'schema':1,'key':'fixture','steps':{}},'typed':typed,'run_child':child,'probe':probe,
      'docker':docker,'image_exists':lambda i:i==image,'heads':lambda:m.checkout_heads(base),'latest_origin':lambda:m.fetched_origin_shas(base),
      'reexec':lambda:(_ for _ in ()).throw(AssertionError('unexpected reexec')),'now':datetime.datetime.now}
m.build_ctx=context
raise SystemExit(m.main(sys.argv[2:]))
'''


def git(*args):
    return subprocess.run(['git','-c','user.name=fixture','-c','user.email=fixture@invalid',*map(str,args)],check=True,text=True,capture_output=True).stdout.strip()


@pytest.mark.parametrize('pending,answer,expected',[(0,'',0),(3,'APPLY MIGRATIONS',0),(3,'no',2)])
def test_exact_owner_entry_with_local_effect_doubles(tmp_path,pending,answer,expected):
    base=tmp_path/'repos';base.mkdir();home=tmp_path/'home';home.mkdir()
    names=('delivery-api','interview-engine','spec-generator','vmodel-contracts','verification-loop','vmodel-spec-app')
    for name in names:
        origin=tmp_path/(name+'.git');git('init','-q','--bare','-b','main',origin)
        checkout=base/name;git('clone','-q',origin,checkout)
        if name=='delivery-api':
            shutil.copytree(ROOT/'scripts',checkout/'scripts')
            (checkout/'.gitignore').write_text('.venv/\n__pycache__/\n')
        git('-C',checkout,'add','-A');git('-C',checkout,'commit','-q','--allow-empty','-m','local fixture')
        git('-C',checkout,'push','-q','origin','main')
    root=base/'delivery-api';venv=root/'.venv/bin';venv.mkdir(parents=True)
    harness=tmp_path/'effect_harness.py';harness.write_text(textwrap.dedent(HARNESS))
    wrapper=venv/'python'
    wrapper.write_text('#!/bin/bash\ncase "$1" in\n-c) exec '+sys.executable+' "$@";;\n*venv_check.py) exit 0;;\n*rebuild.py) exec '+sys.executable+' '+str(harness)+' "$@";;\n*) exit 88;;\nesac\n')
    wrapper.chmod(0o755)
    log=tmp_path/'commands.jsonl';env={**os.environ,'HOME':str(home),'REQUI_ROOT':str(base),'COMMAND_LOG':str(log),
        'PENDING_CODE':str(pending),'GATE_ANSWER':answer,'PYTHONPATH':''}
    result=subprocess.run(['bash',str(root/'scripts/ops/req'),'rebuild','--apply'],env=env,text=True,capture_output=True,timeout=30)
    assert result.returncode==expected,result.stdout+result.stderr
    rows=[json.loads(line) for line in log.read_text().splitlines()]
    assert ['main',[str(root),'--apply']] in rows
    assert git('-C',root,'rev-parse','HEAD')==git('-C',root,'rev-parse','origin/main')
    children=[row[1][2:] for row in rows if row[0]=='child' and row[1][0]=='bash']
    assert ['image','--accept-local'] in children
    assert (['migrate','--apply'] in children)==(pending==3 and answer=='APPLY MIGRATIONS')
    assert (['preview','--apply'] in children)==(expected==0)
    assert any(row[0]=='prompt' for row in rows)==(pending==3)
    if expected==0:
        state=json.loads((home/'rebuild.json').read_text())
        assert {'pull-all','accept-local','preview-restart','cleanup'}<=state['steps'].keys()
