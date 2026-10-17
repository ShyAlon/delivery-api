"""Hermetic check of the wheel requirement reader. Builds tiny fake wheels in a temp folder."""
import importlib.util,zipfile
from pathlib import Path

SPEC=importlib.util.spec_from_file_location('wheel_deps',Path(__file__).resolve().parents[1]/'scripts'/'docker'/'wheel_deps.py')
wheel_deps=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(wheel_deps)


def make(folder,name,requires):
    path=folder/f'{name}-1.0-py3-none-any.whl'
    with zipfile.ZipFile(path,'w') as z:
        z.writestr(f'{name}-1.0.dist-info/METADATA','Metadata-Version: 2.1\nName: %s\nVersion: 1.0\n%s\n'%(name,'\n'.join('Requires-Dist: '+r for r in requires)))


def test_skips_vmodel_contracts_git_urls_and_extras(tmp_path):
    make(tmp_path,'verification_loop',['vmodel-contracts @ git+https://example.invalid/x.git@abc','pydantic>=2.6,<3','pytest>=8; extra == "dev"'])
    make(tmp_path,'delivery_api',['vmodel-contracts==0.28.0','fastapi>=0.110','pydantic>=2.6,<3','tomli>=2; python_version < "3.11"'])
    assert wheel_deps.requirements(str(tmp_path))==['fastapi>=0.110','pydantic>=2.6,<3','tomli>=2; python_version < "3.11"']
