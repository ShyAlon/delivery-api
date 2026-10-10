"""Wrapper dry mode cannot initialize postgres or connect external services."""
import importlib.util,sys
from pathlib import Path
import pytest
spec=importlib.util.spec_from_file_location('item6_container',Path(__file__).parents[1]/'scripts/item6/container_entry.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)

def test_dry_wrapper_invokes_only_dry_preflight(monkeypatch,tmp_path):
 monkeypatch.setattr(sys,'argv',['entry','--config',str(tmp_path/'config.json'),'--run-id','a'*32])
 calls=[];monkeypatch.setattr(m.subprocess,'run',lambda cmd,**kw:calls.append(cmd))
 m.main()
 assert len(calls)==1 and calls[0][-2:]==['--run-id','a'*32] and '--go' not in calls[0]

def test_wrong_go_rejected_before_database_initialization(monkeypatch,tmp_path):
 monkeypatch.setattr(sys,'argv',['entry','--config',str(tmp_path/'config.json'),'--run-id','a'*32,'--go','b'*32])
 calls=[];monkeypatch.setattr(m.subprocess,'run',lambda cmd,**kw:calls.append(cmd))
 with pytest.raises(ValueError,match='exact'):m.main()
 assert len(calls)==1
