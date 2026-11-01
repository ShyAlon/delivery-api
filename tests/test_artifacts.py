"""Writable, portable browser evidence paths for local and CI runs."""
import os,tempfile
from pathlib import Path

def evidence_path(name):
    root=Path(os.environ.get('REQUI_TEST_ARTIFACTS',str(Path(tempfile.gettempdir())/'requi-test-artifacts')))
    result=root/name
    result.parent.mkdir(parents=True,exist_ok=True)
    return str(result)
