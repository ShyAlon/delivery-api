"""The neutral wire schema has one source and reproducible pinned bytes."""
import hashlib
from pathlib import Path
import subprocess
import sys
import zipfile
import requi_child_contract

ROOT=Path(__file__).parents[1]
WHEEL=ROOT/'vendor/requi_child_contract-0.3.0-py3-none-any.whl'


def test_wheel_source_and_installed_contract_are_identical():
    source=(ROOT/'packages/requi-child-contract/src/requi_child_contract/__init__.py').read_bytes()
    with zipfile.ZipFile(WHEEL) as wheel:
        assert wheel.read('requi_child_contract/__init__.py')==source
    assert Path(requi_child_contract.__file__).read_bytes()==source
    before=WHEEL.read_bytes()
    subprocess.run([sys.executable,str(ROOT/'packages/requi-child-contract/scripts/build_wheel.py')],check=True,capture_output=True)
    assert WHEEL.read_bytes()==before


def test_full_image_does_not_try_to_fetch_private_schema_from_pypi():
    import importlib.util
    spec=importlib.util.spec_from_file_location('deps',ROOT/'scripts/docker/wheel_deps.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    assert not any('requi-child-contract' in req for req in module.requirements(str(ROOT/'vendor')))
    assert 'requi_child_contract-0.3.0-py3-none-any.whl /wheels/' in (ROOT/'Dockerfile.full').read_text()


def test_lifecycle_contract_rejects_unknown_identity_or_coercion():
    import json,pytest
    from requi_child_contract import LifecycleEvent,decode_event,encode_event
    event=LifecycleEvent(version=1,operation_id='op-1',sequence=1,state='ready')
    assert decode_event(encode_event(event))==event
    for change in [{'version':True},{'sequence':True},{'sequence':0},{'state':'arbitrary-progress'},{'payload':'private'}]:
        value=event.model_dump(mode='json');value.update(change)
        with pytest.raises(ValueError):decode_event(json.dumps(value).encode()+b'\n')
