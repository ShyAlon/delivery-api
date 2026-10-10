#!/usr/bin/env python3
"""Build exact local wheels + acceptance scripts; no Docker/network/run effects."""
import argparse,hashlib,json,shutil,subprocess
from pathlib import Path
p=argparse.ArgumentParser()
for name in ('delivery','engine','generator','contracts','output'):p.add_argument('--'+name,required=True,type=Path)
a=p.parse_args();out=a.output.resolve()
if out.exists():raise SystemExit('new output directory required')
manifest={}
for name in ('delivery','engine','generator','contracts'):
 root=getattr(a,name).resolve()
 if subprocess.check_output(['git','-C',str(root),'status','--porcelain','--untracked-files=no'],text=True).strip():raise SystemExit('tracked source changes must be committed: '+name)
 inputs=('src','pyproject.toml') if name!='delivery' else ('src','pyproject.toml','scripts/item6','tests/test_item6*')
 if subprocess.check_output(['git','-C',str(root),'ls-files','--others','--exclude-standard',*inputs],text=True).strip():raise SystemExit('uncommitted package input: '+name)
 manifest[name]={'revision':subprocess.check_output(['git','-C',str(root),'rev-parse','HEAD'],text=True).strip()}
out.mkdir(mode=0o700);(out/'wheels').mkdir()
for name in ('contracts','delivery','engine','generator'):
 subprocess.run(['uv','build','--offline','--wheel','--out-dir',str(out/'wheels'),str(getattr(a,name).resolve())],check=True)
for wheel in sorted((out/'wheels').glob('*.whl')):
 digest=hashlib.sha256(wheel.read_bytes()).hexdigest();manifest[wheel.name]={'sha256':digest}
 with (out/'wheel-sha256.txt').open('a') as f:f.write(f'{digest}  /wheels/{wheel.name}\n')
here=Path(__file__).resolve().parent
shutil.copytree(here,out/'item6',ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
shutil.copytree(a.delivery/'supabase/migrations',out/'migrations')
shutil.copyfile(here/'Dockerfile',out/'Dockerfile')
shutil.copyfile(a.delivery/'scripts/docker/tested-constraints.txt',out/'constraints.txt')
(out/'source-manifest.json').write_text(json.dumps(manifest,sort_keys=True,indent=2))
print('Prepared exact local package. Docker build/runtime not verified by preparation. No run started.')
