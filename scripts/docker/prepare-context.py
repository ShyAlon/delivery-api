#!/usr/bin/env python3
"""Build exact local package wheels for a Docker test context, never publish."""
import argparse,json,pathlib,subprocess,shutil,hashlib
p=argparse.ArgumentParser()
for name in ('delivery','engine','generator','contracts','output'):p.add_argument('--'+name,required=True,type=pathlib.Path)
p.add_argument('--constraints',required=True,type=pathlib.Path)
p.add_argument('--ui-test',action='store_true',help='Prepare HTTP + Chromium fake UI test image instead of Stop smoke')
a=p.parse_args();out=a.output.resolve()
if out.exists():raise SystemExit('Output already exists; choose a new empty test-context directory')
repos={name:getattr(a,name).resolve() for name in ('delivery','engine','generator','contracts')}
# Fail before making any output if source identity or tooling is missing.
if shutil.which('uv') is None:raise SystemExit('uv is required to build local wheels')
manifest={}
for name,root in repos.items():
    if not (root/'pyproject.toml').is_file():raise SystemExit(f'Missing package source: {root}')
    sha=subprocess.check_output(['git','-C',str(root),'rev-parse','HEAD'],text=True).strip()
    tracked=subprocess.check_output(['git','-C',str(root),'diff','HEAD','--'],text=True)
    if tracked:raise SystemExit(f'Tracked edits in {name}; commit the intended test snapshot first')
    # Generated scratch diagnostics outside package trees do not affect wheels.
    # Untracked package/build inputs, however, have no source revision identity.
    untracked=subprocess.check_output(['git','-C',str(root),'ls-files','--others','--exclude-standard','src','pyproject.toml'],text=True).strip()
    if untracked:raise SystemExit(f'Untracked package inputs in {name}; commit or remove them before building')
    manifest[name]={'revision':sha}
if not a.constraints.is_file():raise SystemExit('Explicit tested dependency constraints required')
out.mkdir(parents=True);(out/'wheels').mkdir()
for root in repos.values():
    subprocess.run(['uv','build','--offline','--wheel','--out-dir',str(out/'wheels'),str(root)],check=True)
for wheel in (out/'wheels').glob('*.whl'):
    digest=hashlib.sha256(wheel.read_bytes()).hexdigest()
    manifest[wheel.name]={'sha256':digest}
    with (out/'wheel-sha256.txt').open('a') as file:file.write(f'{digest}  /wheels/{wheel.name}\n')
    if a.ui_test:
        names={'vmodel_contracts':'contracts','interview_engine':'engine','spec_generator':'generator','delivery_api':'delivery'}
        package=wheel.name.split('-')[0]
        if package not in names:raise SystemExit('Unexpected UI wheel package')
        (out/('wheel-sha256-'+names[package]+'.txt')).write_text(f'{digest}  /wheels/{wheel.name}\n')
shutil.copyfile(a.constraints,out/'constraints.txt')
here=pathlib.Path(__file__).resolve().parent
shutil.copyfile(here/('Dockerfile.ui-test' if a.ui_test else 'Dockerfile.test'),out/'Dockerfile')
if a.ui_test:
 for name in ('ui-smoke.py','ui-fixtures.py','host-preview.py','run-host-preview.sh','acceptance-smoke.py'):shutil.copyfile(here/name,out/name)
if a.ui_test:
 shutil.copytree(here.parent/'private-ui',out/'private-ui')
 shutil.copyfile(repos['delivery']/'tests/fixtures/revision-source.json',out/'private-ui/revision-source.json')
shutil.copyfile(here/'smoke.py',out/'smoke.py')
shutil.copyfile(here/'run-test-image.sh',out/'run-test-image.sh')
shutil.copyfile(here/'README.md',out/'README.md')
(out/'source-manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
print(f'Prepared built-wheel context: {out}')
print('Image build/run remains required. This command does not certify Docker execution.')
