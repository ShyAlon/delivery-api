#!/usr/bin/env python3
"""Fresh installed-wheel UI check, not Docker/image/platform certification."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import venv


def validate_context(context):
    context = Path(context).resolve()
    manifest = json.loads((context / 'source-manifest.json').read_text())
    wheels = sorted((context / 'wheels').glob('*.whl'))
    if len(wheels) != 4:
        raise ValueError('exact four-package wheel context required')
    expected = {name for name in manifest if name.endswith('.whl')}
    if {wheel.name for wheel in wheels} != expected:
        raise ValueError('wheel names differ from source manifest')
    for wheel in wheels:
        if wheel.is_symlink() or hashlib.sha256(wheel.read_bytes()).hexdigest() != manifest[wheel.name]['sha256']:
            raise ValueError('wheel content differs from source manifest')
    hash_lines=(context / 'wheel-sha256.txt').read_text().splitlines()
    expected_lines={manifest[wheel.name]['sha256']+'  /wheels/'+wheel.name for wheel in wheels}
    if len(hash_lines)!=4 or set(hash_lines)!=expected_lines:
        raise ValueError('Docker wheel hashes differ from source manifest')
    for name in ('constraints.txt', 'ui-smoke.py', 'Dockerfile'):
        if not (context / name).is_file():
            raise ValueError('complete UI context required')
    docker = (context / 'Dockerfile').read_text()
    # Keep this explicit contract tied to the Docker install command, not dev
    # extras or pip freeze from a working development environment.
    legacy='pip install --no-cache-dir -c /app/constraints.txt /wheels/*.whl playwright==1.62.0' in docker
    if not legacy:
        packages=('vmodel_contracts','interview_engine','spec_generator','delivery_api')
        roles=('contracts','engine','generator','delivery')
        expected=['RUN pip install --no-cache-dir -c /app/constraints.txt playwright==1.62.0 && python -m playwright install --with-deps chromium']
        for package,role in zip(packages,roles):
            selected=[w for w in wheels if w.name.startswith(package+'-')]
            if len(selected)!=1:raise ValueError('exact named repo wheel required')
            wheel=selected[0]
            if (context/('wheel-sha256-'+role+'.txt')).read_text()!=manifest[wheel.name]['sha256']+'  /wheels/'+wheel.name+'\n':
                raise ValueError('per-repo Docker hashes differ from source manifest')
            expected.append('RUN sha256sum --check /app/wheel-sha256-'+role+'.txt && pip install --no-cache-dir -c /app/constraints.txt /wheels/'+package+'-*.whl')
        expected.append('RUN sha256sum --check /app/wheel-sha256.txt && pip check')
        if [line for line in docker.splitlines() if line.startswith('RUN ')]!=expected:
            raise ValueError('Docker layered dependency command changed; update clean verifier')
    return context, wheels


def clean_environment(root):
    # Install may download dependencies. Runtime receives no source path, user
    # dotenv, provider key, cloud DB setting or account credentials.
    return {'PATH': os.defpath, 'HOME': str(root), 'LANG': 'C.UTF-8',
            'PYTHONNOUSERSITE': '1', 'PYTHONDONTWRITEBYTECODE': '1',
            'DELIVERY_DATABASE_URL': 'sqlite:///' + str(root / 'fake-ui.db'),
            'XDG_DATA_HOME': str(root / 'data'), 'REQUI_RE_DIST': '1'}


def verify(context, *, browser_path=None):
    context, wheels = validate_context(context)
    with tempfile.TemporaryDirectory(prefix='requi-clean-wheel-') as scratch:
        root = Path(scratch)
        venv.EnvBuilder(with_pip=True, system_site_packages=False).create(root / 'venv')
        python = root / 'venv' / 'bin' / 'python'
        env = clean_environment(root)
        command = [str(python), '-m', 'pip']
        subprocess.run(command + ['list', '--format=freeze'], check=True, cwd=root, env=env)
        install=command + ['install', '--no-cache-dir', '-c', str(context / 'constraints.txt')]
        docker=(context/'Dockerfile').read_text()
        if 'wheel-sha256-contracts.txt' in docker:
            subprocess.run(install+['playwright==1.62.0'],check=True,cwd=root,env=env)
            for package in ('vmodel_contracts','interview_engine','spec_generator','delivery_api'):
                wheel=next(w for w in wheels if w.name.startswith(package+'-'))
                subprocess.run(install+[str(wheel)],check=True,cwd=root,env=env)
        else:
            subprocess.run(install+list(map(str,wheels))+['playwright==1.62.0'],check=True,cwd=root,env=env)
        subprocess.run(command + ['check'], check=True, cwd=root, env=env)
        if browser_path is None:
            subprocess.run([str(python), '-m', 'playwright', 'install', 'chromium'], check=True, cwd=root, env=env)
        else:
            browser_path = Path(browser_path).resolve()
            if not browser_path.is_dir():
                raise ValueError('explicit installed browser directory required')
            env['PLAYWRIGHT_BROWSERS_PATH'] = str(browser_path)
        subprocess.run([str(python), str(context / 'ui-smoke.py')], check=True, cwd=root, env=env)
        if (context/'acceptance-smoke.py').is_file():
            subprocess.run([str(python), str(context/'acceptance-smoke.py')],check=True,cwd=root,env=env)
    print(json.dumps({'clean_installed_wheel_ui_smoke': 'passed', 'docker_executed': False,
                      'system_site_packages': False, 'dev_extras': False}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('context', type=Path)
    parser.add_argument('--browser-path', type=Path, help='Explicit existing Chromium cache; never a Python dependency source')
    args = parser.parse_args()
    verify(args.context, browser_path=args.browser_path)
