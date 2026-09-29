"""The updater must not confuse a Git SHA pin change with a package-version bump."""
import os
import subprocess
from pathlib import Path


def test_pull_all_reinstalls_contracts_without_syncing_contracts_source(tmp_path):
    root=tmp_path/'require';bin_dir=tmp_path/'bin';root.mkdir();bin_dir.mkdir()
    for name in ('delivery-api','interview-engine','spec-generator','vmodel-contracts','verification-loop','vmodel-spec-app'):
        repo=root/name;(repo/'.git').mkdir(parents=True)
        if name!='vmodel-spec-app':
            (repo/'pyproject.toml').write_text('[project]\ndependencies = ["vmodel-contracts @ git+https://example.invalid/contracts.git@aabbcc"]\n')
        if name not in ('vmodel-contracts','vmodel-spec-app'):(repo/'uv.lock').touch()
    for name,script in {
        'git': '#!/bin/sh\ncase "$*" in *"status --porcelain"*) exit 0;; *"rev-parse HEAD"*) echo aabbccdd;; *"pull --ff-only"*) exit 0;; esac\nexit 1\n',
        'uv': '#!/bin/sh\nprintf "%s|%s\\n" "$PWD" "$*" >> "$UV_LOG"\n',
    }.items():
        path=bin_dir/name;path.write_text(script);path.chmod(0o755)
    script=Path(__file__).resolve().parents[1]/'scripts'/'pull-all.sh'
    env={**os.environ,'PATH':f'{bin_dir}:{os.environ["PATH"]}','REQUI_RE_REPOS_ROOT':str(root),'UV_LOG':str(tmp_path/'uv.log')}
    result=subprocess.run(['bash',str(script)],env=env,capture_output=True,text=True,check=True)
    lines=(tmp_path/'uv.log').read_text().splitlines()
    assert len(lines)==4
    assert all('--locked --quiet --reinstall-package vmodel-contracts' in line for line in lines)
    assert all('/vmodel-contracts|' not in line for line in lines)
    assert 'Notice: vmodel-contracts is the source package' in result.stdout
    assert 'Error:' not in result.stdout+result.stderr
