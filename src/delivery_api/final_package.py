"""Allowlisted immutable final exports; artifacts/ is never recursively zipped."""
from pathlib import Path
import hashlib,io,json,zipfile,subprocess,os,sys

class PackageBlocked(ValueError):pass

def producer_handoff(interview,spec,baseline):
    built=os.environ.get('REQUI_RE_DIST')=='1'
    repo=Path.cwd() if built else Path(os.environ.get('SPEC_GENERATOR_DIR',str(Path(__file__).resolve().parents[3]/'spec-generator')))
    python=Path(sys.executable) if built else repo/'.venv/bin/python'
    if not python.is_file():raise PackageBlocked('Spec-generator export environment is unavailable')
    # Export is a zero-model validation of delivery's retained projection, not
    # a generator run. The CLI requires a claimed checkpoint runtime and must
    # never be invoked here without one.
    command=[str(python),'-c',
        'import json,sys; from pathlib import Path; from spec_generator.handoff import build_handoff; print(json.dumps(build_handoff(Path(sys.argv[1]),Path(sys.argv[2]))))',
        str(interview),str(spec)]
    env={**os.environ,'PYTHONPATH':str(repo/'src')+os.pathsep+os.environ.get('PYTHONPATH','')}
    result=subprocess.run(command,env=env,cwd=repo,capture_output=True,text=True,timeout=30)
    if result.returncode:raise PackageBlocked('Producer export failed: '+result.stderr[-1500:])
    try:return json.loads(result.stdout)
    except ValueError as exc:raise PackageBlocked('Producer returned an invalid handoff') from exc

def package(task,session,handoff,folder:Path):
    if task['state']!='done' or any(p['state'] not in ('approved','done') for p in task['phases']):raise PackageBlocked('All phases must finish and be approved before final packaging')
    from vmodel_contracts.handoff import CodingHandoff
    try:handoff=CodingHandoff.model_validate(handoff).model_dump(mode='json')
    except ValueError as exc:raise PackageBlocked('Invalid producer handoff: '+str(exc)) from exc
    if handoff['session_id']!=session:raise PackageBlocked('Export belongs to another run')
    if handoff.get('blocked_reasons'):raise PackageBlocked('Open founder inputs block final package: '+'; '.join(handoff['blocked_reasons']))
    files=handoff['files']
    required={'README.md','founder-decisions.json','founder-decisions.md','decisions.json','interview/intent_model.json','interview/conops.json','spec/needs.json','spec/srs.json','spec/architecture.json','spec/items.json','spec/tests.json','spec/AGENTS.md','spec/manifest.json','spec/traceability.json'}
    if not required.issubset(files):raise PackageBlocked('Missing export outputs: '+', '.join(sorted(required-set(files))))
    expected={'README.md','founder-decisions.json','founder-decisions.md','decisions.json','interview/intent_model.json','interview/conops.json','spec/needs.json','spec/srs.json','spec/architecture.json','spec/items.json','spec/tests.json','spec/validation.json','spec/AGENTS.md','spec/manifest.json','spec/traceability.json','spec/system-baseline.json'}
    expected|={'interview/semantic-review.json','spec/srs.semantic-review.json'}
    expected|={'docs/'+name+'.md' for name in ('README','intent','conops','needs','architecture','requirements','tests','validation','traceability','findings','changes','run')}
    companions={n[:-5]+'.md' for n in expected if n.endswith('.json') and n.startswith(('interview/','spec/'))}
    for name,text in files.items():
        path=Path(name)
        safe_spec=__import__('re').fullmatch(r'spec/specs/SPEC-\d+\.md',name)
        safe_doc=__import__('re').fullmatch(r'docs/(?:specs/SPEC-[1-9][0-9]*|tests/T-[1-9][0-9]*|review/round-[1-9][0-9]*)\.md',name)
        if path.is_absolute() or '..' in path.parts or (name not in expected|companions and not safe_spec and not safe_doc):raise PackageBlocked('Unexpected export file: '+name)
        if len(text.encode())>2_000_000:raise PackageBlocked('Export file exceeds limit: '+name)
        if hashlib.sha256(text.encode()).hexdigest()!=handoff['sha256'].get(name):raise PackageBlocked('Export checksum mismatch: '+name)
    if sum(len(x.encode()) for x in files.values())>20_000_000:raise PackageBlocked('Export exceeds total limit')
    manifest={'version':1,'task_id':task['task_id'],'session_id':session,'generation':task['generation'],'phase_revisions':{p['phase_key']:{'completed_at':p['completed_at'],'state':p['state']} for p in task['phases']},'sha256':handoff['sha256']}
    identity=hashlib.sha256(json.dumps(manifest,sort_keys=True).encode()).hexdigest();manifest['package_id']=identity
    folder.mkdir(parents=True,exist_ok=True);destination=folder/(identity+'.zip')
    if not destination.exists():
        stream=io.BytesIO()
        with zipfile.ZipFile(stream,'w',zipfile.ZIP_DEFLATED) as archive:
            for name,text in sorted(files.items()):archive.writestr(name,text)
            archive.writestr('package.json',json.dumps(manifest,indent=2))
        with zipfile.ZipFile(io.BytesIO(stream.getvalue())) as archive:
            for name,expected_hash in manifest['sha256'].items():
                if hashlib.sha256(archive.read(name)).hexdigest()!=expected_hash:raise PackageBlocked('ZIP checksum verification failed')
        temporary=destination.with_suffix('.tmp');temporary.write_bytes(stream.getvalue());temporary.replace(destination)
    return {'package_id':identity,'generation':task['generation'],'contents':sorted(files),'path':destination}
