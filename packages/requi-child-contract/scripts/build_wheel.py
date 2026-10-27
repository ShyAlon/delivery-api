"""Build the tiny pure-Python wheel reproducibly with stdlib only.

The same schema source/package metadata also supports normal PEP-517 builds.
This offline builder prevents machine/time-dependent vendored binary drift.
"""
import base64
import csv
import hashlib
import io
from pathlib import Path
import zipfile

root=Path(__file__).resolve().parents[1]
output=root.parents[1]/'vendor'/'requi_child_contract-0.3.0-py3-none-any.whl'
info='requi_child_contract-0.3.0.dist-info'
files={
    'requi_child_contract/__init__.py':(root/'src/requi_child_contract/__init__.py').read_bytes(),
    f'{info}/METADATA':b'Metadata-Version: 2.1\nName: requi-child-contract\nVersion: 0.3.0\nRequires-Python: >=3.10\nRequires-Dist: pydantic>=2.6,<3\nRequires-Dist: httpx>=0.27\n\n',
    f'{info}/WHEEL':b'Wheel-Version: 1.0\nGenerator: requi-offline-contract-builder\nRoot-Is-Purelib: true\nTag: py3-none-any\n',
}
for module in ('broker','compute'):
    files['requi_child_contract/'+module+'.py']=(root/('src/requi_child_contract/'+module+'.py')).read_bytes()
record=io.StringIO(newline='');writer=csv.writer(record,lineterminator='\n')
for name,data in sorted(files.items()):
    digest=base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b'=').decode()
    writer.writerow((name,'sha256='+digest,len(data)))
writer.writerow((f'{info}/RECORD','',''));files[f'{info}/RECORD']=record.getvalue().encode()
output.parent.mkdir(parents=True,exist_ok=True)
with zipfile.ZipFile(output,'w',compression=zipfile.ZIP_DEFLATED) as wheel:
    for name,data in sorted(files.items()):
        item=zipfile.ZipInfo(name,date_time=(2026,10,27,0,0,0));item.compress_type=zipfile.ZIP_DEFLATED
        item.external_attr=0o644<<16;wheel.writestr(item,data)
print(output)
print(hashlib.sha256(output.read_bytes()).hexdigest())
