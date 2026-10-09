from pathlib import Path
import subprocess

ROOT=Path(__file__).parents[1]


def test_integrated_test_image_uses_wheels_not_private_git_or_live_source():
    docker=(ROOT/'scripts/docker/Dockerfile.test').read_text()
    assert 'COPY wheels/' in docker and 'pip check' in docker
    assert 'COPY . ' not in docker and 'gitconfig' not in docker
    assert 'CMD ["python", "/app/smoke.py"]' in docker
    constraints=(ROOT/'scripts/docker/tested-constraints.txt').read_text()
    assert '@ file:' not in constraints and 'typesafe-sdk==0.7.0' in constraints


def test_test_runner_never_mounts_user_data_or_enables_provider_network():
    runner=ROOT/'scripts/docker/run-test-image.sh'
    subprocess.run(['bash','-n',str(runner)],check=True)
    script=runner.read_text()
    assert '--network none' in script and '--read-only' in script and '--cap-drop ALL' in script
    run_line=next(line for line in script.splitlines() if line.startswith('docker run '))
    assert '--env-file' not in run_line and ' -v ' not in run_line and 'docker push' not in script


def test_test_context_preparer_fails_before_output_on_missing_source(tmp_path):
    output=tmp_path/'out'
    result=subprocess.run(['python3',str(ROOT/'scripts/docker/prepare-context.py'),
        '--delivery',str(tmp_path/'absent'),'--engine',str(tmp_path/'absent'),
        '--generator',str(tmp_path/'absent'),'--contracts',str(tmp_path/'absent'),
        '--output',str(output),'--constraints',str(ROOT/'scripts/docker/tested-constraints.txt')],capture_output=True,text=True)
    assert result.returncode!=0 and not output.exists()


def test_image_checks_exact_wheel_hashes_and_does_not_infer_cloud_gate_from_fake_flag():
    docker=(ROOT/'scripts/docker/Dockerfile.test').read_text()
    assert 'sha256sum --check /app/wheel-sha256.txt' in docker
    assert 'DELIVERY_CLOUD_TENANTS' not in docker
    assert 'DELIVERY_DATABASE_URL=sqlite:////tmp/test-delivery.db' in docker
    prepare=(ROOT/'scripts/docker/prepare-context.py').read_text()
    assert "'ls-files','--others','--exclude-standard','src','pyproject.toml'" in prepare
    assert "'run-test-image.sh'" in prepare and "'README.md'" in prepare


def test_ui_image_exercises_actual_listeners_and_browser_not_only_stop():
    docker=(ROOT/'scripts/docker/Dockerfile.ui-test').read_text()
    assert 'playwright==1.62.0' in docker and 'install --with-deps chromium' in docker
    assert 'CMD ["python", "/app/ui-smoke.py"]' in docker
    smoke=(ROOT/'scripts/docker/ui-smoke.py').read_text()
    assert 'TenantMiddleware' in smoke and 'uvicorn.Server' in smoke
    assert 'expect_download' in smoke and "active[0]=False" in smoke
    assert "page.route('**/*',isolate)" in smoke
    assert 'Fake identity/data only' in smoke


def test_host_preview_is_explicit_loopback_internal_and_fake_only():
    script=(ROOT/'scripts/docker/run-host-preview.sh').read_text()
    subprocess.run(['bash','-n',str(ROOT/'scripts/docker/run-host-preview.sh')],check=True)
    assert 'network create --internal' in script and '"127.0.0.1:$WEB:8000"' in script
    assert '"127.0.0.1:$BACK:8020"' in script and '"$MAJOR" -ge 28' in script
    run_line=next(line for line in script.splitlines() if line.startswith('docker run '))
    assert '--env-file' not in run_line and ' -v ' not in run_line and '--cap-drop ALL' in run_line
    fixture=(ROOT/'scripts/docker/host-preview.py').read_text()
    assert 'Preview denies non-loopback egress' in fixture and 'Fake preview is read-only' in fixture
    assert 'stop.wait()' in fixture
    assert "fixture['FAKE_TOKEN']" in fixture
    assert 'fake-ui-only' in (ROOT/'scripts/docker/ui-fixtures.py').read_text()


def _clean_verifier():
    import importlib.util
    spec=importlib.util.spec_from_file_location('clean_verifier',ROOT/'scripts/docker/verify-clean-install.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def test_clean_install_does_not_inherit_credentials_dev_sources_or_site_packages(tmp_path,monkeypatch):
    module=_clean_verifier()
    for key in ('OPENAI_API_KEY','DELIVERY_POSTGRES_DSN','PYTHONPATH','TURSO_DATABASE_URL','PIP_EXTRA_INDEX_URL'):
        monkeypatch.setenv(key,'must-not-inherit')
    env=module.clean_environment(tmp_path)
    assert not any(value=='must-not-inherit' for value in env.values())
    assert env['PYTHONNOUSERSITE']=='1'
    assert env['DELIVERY_DATABASE_URL']=='sqlite:///'+str(tmp_path/'fake-ui.db')
    source=(ROOT/'scripts/docker/verify-clean-install.py').read_text()
    assert 'system_site_packages=False' in source and 'dev]' not in source


def test_clean_install_checks_exact_wheels_before_creating_environment(tmp_path):
    import hashlib,json,pytest
    module=_clean_verifier();(tmp_path/'wheels').mkdir();manifest={}
    for name,role in (('delivery_api','delivery'),('interview_engine','engine'),('spec_generator','generator'),('vmodel_contracts','contracts')):
        wheel=tmp_path/'wheels'/f'{name}-1.0-py3-none-any.whl';wheel.write_bytes(name.encode())
        manifest[wheel.name]={'sha256':hashlib.sha256(wheel.read_bytes()).hexdigest()}
        (tmp_path/('wheel-sha256-'+role+'.txt')).write_text(manifest[wheel.name]['sha256']+'  /wheels/'+wheel.name+'\n')
    (tmp_path/'source-manifest.json').write_text(json.dumps(manifest))
    (tmp_path/'wheel-sha256.txt').write_text(''.join(value['sha256']+'  /wheels/'+name+'\n' for name,value in manifest.items()))
    (tmp_path/'constraints.txt').write_text('')
    (tmp_path/'ui-smoke.py').write_text('')
    (tmp_path/'Dockerfile').write_text((ROOT/'scripts/docker/Dockerfile.ui-test').read_text())
    assert len(module.validate_context(tmp_path)[1])==4
    wheel.write_bytes(b'changed')
    with pytest.raises(ValueError,match='manifest'):module.validate_context(tmp_path)
    wheel.write_bytes(name.encode())
    (tmp_path/'wheel-sha256-contracts.txt').write_text('wrong')
    with pytest.raises(ValueError,match='per-repo Docker hashes'):module.validate_context(tmp_path)


def test_host_preview_content_tag_reuses_image_and_changes_on_content_not_mtime(tmp_path):
    import os,json
    context=tmp_path/'context';context.mkdir();(context/'host-preview.py').write_text('fake fixture')
    commands=tmp_path/'commands';commands.mkdir();log=tmp_path/'docker.log';cache=tmp_path/'cache';cache.mkdir()
    docker=commands/'docker'
    docker.write_text('''#!/usr/bin/env python3
import os,sys,json,pathlib
args=sys.argv[1:]
with open(os.environ['FAKE_DOCKER_LOG'],'a') as f:f.write(json.dumps(args)+'\\n')
cache=pathlib.Path(os.environ['FAKE_DOCKER_CACHE'])
if args[0]=='version':print('28.1.0')
elif args[:2]==['image','inspect']:sys.exit(0 if (cache/args[2].replace(':','_')).exists() else 1)
elif args[0]=='build':(cache/args[2].replace(':','_')).touch()
''');docker.chmod(0o755)
    curl=commands/'curl';curl.write_text('#!/bin/sh\nexit 0\n');curl.chmod(0o755)
    env=os.environ|{'PATH':str(commands)+os.pathsep+os.environ['PATH'],'FAKE_DOCKER_LOG':str(log),'FAKE_DOCKER_CACHE':str(cache)}
    runner=str(ROOT/'scripts/docker/run-host-preview.sh')
    def run():
        log.write_text('');subprocess.run(['bash',runner,str(context)],check=True,env=env,capture_output=True)
        return [json.loads(line) for line in log.read_text().splitlines()]
    first=run();build=next(c for c in first if c[0]=='build');tag=build[2]
    assert len(tag.split(':')[1])==64
    os.utime(context/'host-preview.py',None)
    second=run();assert not any(c[0]=='build' for c in second)
    assert ['image','inspect',tag] in second
    assert any(c[0]=='run' and '--network' in c and 'none' in c for c in second)
    assert any(c[:3]==['network','create','--internal'] for c in second)
    (context/'host-preview.py').write_text('changed fake fixture')
    third=run();assert next(c for c in third if c[0]=='build')[2]!=tag
    (context/'bad-link').symlink_to(context/'host-preview.py')
    assert subprocess.run(['bash',runner,str(context)],env=env,capture_output=True).returncode!=0


def test_ui_layer_order_and_checksums_keep_unrelated_wheels_out_of_early_layers():
    docker=(ROOT/'scripts/docker/Dockerfile.ui-test').read_text()
    assert 'COPY wheels/ /wheels/' not in docker
    packages=('vmodel_contracts','interview_engine','spec_generator','delivery_api')
    positions=[docker.index('COPY wheels/'+p+'-*.whl') for p in packages]
    assert positions==sorted(positions)
    assert docker.index('install --with-deps chromium')<positions[0]
    assert docker.index('COPY wheel-sha256.txt')>positions[-1]
    for package,role in zip(packages,('contracts','engine','generator','delivery')):
        assert 'sha256sum --check /app/wheel-sha256-'+role+'.txt && pip install --no-cache-dir -c /app/constraints.txt /wheels/'+package+'-*.whl' in docker
