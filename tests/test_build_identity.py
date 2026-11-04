"""The Debug dialog shows which build is running, on the full image where no MANIFEST.txt exists."""
from pathlib import Path

import delivery_api.app as module
from delivery_api import config

ROOT=Path(__file__).resolve().parents[1]


def test_the_build_id_from_the_image_is_the_runtime_id(monkeypatch):
    monkeypatch.setenv('REQUI_BUILD_ID','806db1d3-17ee1ff1-a425cbde-690648ab')
    assert module._runtime_version()['runtime_id']=='806db1d3-17ee1ff1-a425cbde-690648ab'


def test_a_blank_build_id_is_ignored_and_the_manifest_search_still_answers(monkeypatch):
    for value in ('','  '):
        monkeypatch.setenv('REQUI_BUILD_ID',value)
        assert module._runtime_version()['runtime_id'].strip()!=''
    monkeypatch.delenv('REQUI_BUILD_ID',raising=False)
    assert module._runtime_version()['runtime_id']


def test_cloud_health_names_the_build_and_its_repositories_and_nothing_about_the_tenants(monkeypatch):
    # The repository commit ids are baked into the image env and printed at startup;
    # they carry nothing about tenants. The exact-dict assertion keeps everything else out.
    monkeypatch.setattr(config,'CLOUD_TENANTS',True)
    out=module.health()
    assert out=={'ok':True,'runtime_id':module.RUNTIME_VERSION['runtime_id'],'repositories':module.RUNTIME_VERSION['repositories'],'built_at':module.RUNTIME_VERSION.get('built_at')}


def test_the_image_receives_the_build_id_from_the_build_script():
    docker=(ROOT/'Dockerfile.full').read_text()
    assert 'ARG REQUI_BUILD_ID=unknown\nENV REQUI_BUILD_ID=$REQUI_BUILD_ID\n' in docker.split('USER 10001:10001')[0]
    # After the dependency layer, so a new build id does not rebuild the pip install.
    assert docker.index('pip install --no-cache-dir -r /wheels/requirements.txt')<docker.index('ARG REQUI_BUILD_ID')
    script=(ROOT/'scripts'/'docker'/'build-full-image.sh').read_text()
    assert 'docker build --build-arg REQUI_BUILD_ID=$TAG -f delivery-api/Dockerfile.full' in script
