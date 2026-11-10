"""Owner debug bundle: every log that matters - run-dir evidence, docker, host -
in ONE ZIP whose path prints last. Built for the manual-paste pain of
2026-11-09: browser-smoke.log, docker logs and state.json were gathered by
hand, one paste at a time. Read-only by construction: never reads env files,
credentials, Docker Env or argv; never starts, stops or writes to anything it
reads. Every text payload passes the secret scrubber; docker inspect
contributes named safe fields only. A missing source is named in facts.json
and on stderr - the ZIP still ships with everything that exists.
"""
import argparse,datetime,json,os,re,subprocess,sys,zipfile
from pathlib import Path
from delivery_api.debug_export import redact
p=argparse.ArgumentParser();p.add_argument('--code',default='manual');p.add_argument('--step',default='');p.add_argument('--run-dir',default='');args=p.parse_args()
root=Path(__file__).resolve().parents[2];target=root/'runs'/'preview-debug';target.mkdir(parents=True,exist_ok=True)
home=Path.home();missing=[]
LIMIT=200000
def read(command,timeout=15):
    try:return subprocess.run(command,cwd=root,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,timeout=timeout).stdout[-LIMIT:]
    except (OSError,subprocess.TimeoutExpired) as exc:return type(exc).__name__
def clean(text):
    text=re.sub(r'postgres(?:ql)?://[^\s]+','postgresql://[REDACTED]',text)
    text=re.sub(r'\bsk-[A-Za-z0-9_-]+','[REDACTED]',text)
    return redact(text)
def tail(path):
    size=path.stat().st_size
    with open(path,'rb') as f:
        if size>LIMIT:f.seek(size-LIMIT)
        return f.read().decode('utf-8','replace')
def repos():
    base=Path(os.environ.get('REQUI_ROOT',str(home/'development'/'require')))
    out={}
    if base.is_dir():
        for d in sorted(base.iterdir()):
            if not (d/'.git').exists():continue
            # Porcelain lines are 'XY path' (tracked edits, '??' untracked). Names only, no content, bounded.
            changed=[l.strip() for l in read(['git','-C',str(d),'status','--porcelain']).splitlines() if l.strip()]
            out[d.name]={'branch':read(['git','-C',str(d),'rev-parse','--abbrev-ref','HEAD']).strip(),
                         'head':read(['git','-C',str(d),'rev-parse','HEAD']).strip(),
                         'dirty':len(changed),'dirty_files':changed[:20]}
    if not out:missing.append('no git repos under '+str(base))
    return out
def pick_run_dir():
    if args.run_dir:
        d=Path(args.run_dir)
        if not d.is_absolute():d=home/'.local/share/requi/image-workflow'/args.run_dir
        if d.is_dir():return d
        missing.append('named run dir not found: '+str(d));return None
    base=home/'.local/share/requi/image-workflow'
    states=sorted(base.glob('*/state.json'),key=lambda p:p.stat().st_mtime,reverse=True) if base.is_dir() else []
    if states:return states[0].parent
    missing.append('no image-workflow run dirs under '+str(base));return None
def containers():
    names=[n.strip() for n in read(['docker','ps','-a','--format','{{.Names}}']).splitlines() if n.strip().startswith('requi-preview-exec')]
    if not names:missing.append('no requi-preview-exec* containers in docker ps -a')
    return names
INSPECT='{"name":{{json .Name}},"image":{{json .Config.Image}},"status":{{json .State.Status}},"started_at":{{json .State.StartedAt}},"exit_code":{{json .State.ExitCode}},"error":{{json .State.Error}}}'
def host_facts():
    lines=[read(['uname','-a']).strip()]
    if sys.platform=='darwin':lines.append(read(['sw_vers']).strip())
    lines.append('docker version: '+read(['docker','version','--format','{{.Server.Version}}']).strip())
    return '\n'.join(lines)
now=datetime.datetime.now(datetime.timezone.utc)
repo_facts=repos();run_dir=pick_run_dir();names=containers()
rebuild_state=home/'.local/share/requi/rebuild/state.json'
if not rebuild_state.is_file():rebuild_state=None;missing.append('no rebuild state at '+str(home/'.local/share/requi/rebuild/state.json'))
facts={'captured_at':now.isoformat(),'failure_code':args.code,'step':args.step,'delivery_head':repo_facts.get('delivery-api',{}).get('head',''),'repos':repo_facts,
       'run_dir':str(run_dir) if run_dir else None,'containers':names,'missing':missing,
       'omissions':['No settings/env/credentials/argv or database data read','docker inspect contributes name/image/status/started_at/exit_code/error only - never Env or Mounts','Logs are bounded tails; rotated-away output cannot be recovered']}
path=target/('preview-'+now.strftime('%Y%m%dT%H%M%S%fZ')+'.zip')
fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
with os.fdopen(fd,'wb') as out,zipfile.ZipFile(out,'w',zipfile.ZIP_DEFLATED) as z:
    z.writestr('facts.json',json.dumps(facts,indent=2))
    z.writestr('host.txt',clean(host_facts()))
    z.writestr('repos.txt','\n'.join('%s %s %s dirty=%s %s'%(n,r['branch'],r['head'],r['dirty'],'; '.join(r['dirty_files'])) for n,r in repo_facts.items()))
    z.writestr('images.txt',clean(read(['docker','images','--format','{{.Repository}}:{{.Tag}} {{.ID}} {{.CreatedSince}}','--filter','reference=requi*'])))
    if run_dir:
        for f in sorted(run_dir.iterdir()):
            if f.is_file():z.writestr('run/'+run_dir.name+'/'+f.name,clean(tail(f)))
    if rebuild_state:z.writestr('rebuild-state.json',clean(tail(rebuild_state)))
    if names:
        z.writestr('containers.txt','\n'.join(clean(read(['docker','inspect','--format',INSPECT,n])) for n in names))
        for n in names:z.writestr('docker-'+n+'.log',clean(read(['docker','logs','--tail','300',n])))
for m in missing:print('MISSING: '+m,file=sys.stderr)
print('Debug ZIP: '+str(path));print('Failure: '+args.code+'; step: '+args.step+'; build: '+facts['delivery_head'])
