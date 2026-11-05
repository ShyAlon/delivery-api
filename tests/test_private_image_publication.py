import importlib.util
import json
from pathlib import Path
import sys
import pytest

D=Path(__file__).resolve().parents[1]/'scripts/docker';sys.path.insert(0,str(D))
spec=importlib.util.spec_from_file_location('private_publication',D/'private_publication.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)


def test_public_or_unknown_package_never_publishes(monkeypatch):
    for visibility in ('public','internal',None):
        monkeypatch.setattr(m,'command',lambda *a,**k:json.dumps({'name':'require','package_type':'container','visibility':visibility,'owner':{'login':'shyalon'}}))
        with pytest.raises(m.WorkflowError):m.require_private('ghcr.io/shyalon/require')


def test_private_package_and_owner_are_verified(monkeypatch):
    calls=[]
    def run(args,**kwargs):
        calls.append(args);return json.dumps({'name':'require','package_type':'container','visibility':'private','owner':{'login':'ShyAlon'}})
    monkeypatch.setattr(m,'command',run)
    m.require_private('ghcr.io/shyalon/require')
    assert calls==[['gh','api','/users/shyalon/packages/container/require']]


def test_wrong_owner_refused(monkeypatch):
    monkeypatch.setattr(m,'command',lambda *a,**k:json.dumps({'name':'require','package_type':'container','visibility':'private','owner':{'login':'someone-else'}}))
    with pytest.raises(m.WorkflowError):m.require_private('ghcr.io/shyalon/require')


def test_publish_requires_matching_accepted_id_before_tag_or_push(monkeypatch):
    monkeypatch.setattr(m,'require_private',lambda value:None)
    class State:state={'stages':{'build':{'image_id':'built'}}}
    with pytest.raises(m.WorkflowError,match='differs'):m.publish('ghcr.io/shyalon/require',State(),{'image_id':'other'})
