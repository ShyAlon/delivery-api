"""Bounded local failure ZIP. Never reads env files, credentials, Docker env or argv."""
import argparse,datetime,json,os,re,subprocess,zipfile
from pathlib import Path
from delivery_api.debug_export import redact
p=argparse.ArgumentParser();p.add_argument('--code',default='manual');p.add_argument('--step',default='');args=p.parse_args()
root=Path(__file__).resolve().parents[2];target=root/'runs'/'preview-debug';target.mkdir(parents=True,exist_ok=True)
def read(command):
    try:return subprocess.run(command,cwd=root,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,timeout=10).stdout[-200000:]
    except (OSError,subprocess.TimeoutExpired) as exc:return type(exc).__name__
def clean(text):
    text=re.sub(r'postgres(?:ql)?://[^\s]+','postgresql://[REDACTED]',text)
    text=re.sub(r'\bsk-[A-Za-z0-9_-]+','[REDACTED]',text)
    return redact(text)
now=datetime.datetime.now(datetime.timezone.utc);path=target/('preview-'+now.strftime('%Y%m%dT%H%M%S%fZ')+'.zip')
facts={'captured_at':now.isoformat(),'failure_code':args.code,'step':args.step,'delivery_head':read(['git','rev-parse','HEAD']).strip(),'container':'requi-preview-exec','omissions':['No settings/env/credentials/argv or database data read','Logs are bounded current container output only; unavailable/removed container output cannot be recovered']}
fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
with os.fdopen(fd,'wb') as out,zipfile.ZipFile(out,'w',zipfile.ZIP_DEFLATED) as z:
    z.writestr('facts.json',json.dumps(facts,indent=2));z.writestr('container.log',clean(read(['docker','logs','--tail','300','requi-preview-exec'])))
print('Debug ZIP: '+str(path));print('Failure: '+args.code+'; step: '+args.step+'; build: '+facts['delivery_head'])
