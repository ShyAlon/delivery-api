"""Retired preview settings cannot suppress normal reads or downloads."""
from types import SimpleNamespace
from pathlib import Path
from fastapi.testclient import TestClient
import pytest

@pytest.mark.parametrize('origin',['http://localhost:8000','https://remote.example'])
def test_retired_preview_input_does_not_change_boot_or_auth(monkeypatch,origin):
 import delivery_api.app as module
 monkeypatch.setenv('DELIVERY_PUBLIC_ORIGIN',origin)
 monkeypatch.setenv('DELIVERY_LOCAL_PREVIEW','1');monkeypatch.setenv('DELIVERY_PREVIEW','1')
 c=TestClient(module.app)
 assert 'local_preview' not in c.get('/api/auth').json()
 assert 'preview' not in c.get('/api/auth').json()
 assert '"preview": true' not in c.get('/').text

def test_phase_output_uses_regular_reader_despite_retired_preview_input(monkeypatch):
 import delivery_api.app as module
 monkeypatch.setenv('DELIVERY_LOCAL_PREVIEW','1')
 monkeypatch.setattr(module,'store',SimpleNamespace(task=lambda _: {'active_phase':'srs','generation':3,'phases':[]}))
 monkeypatch.setattr(module,'_recorded_engine_session',lambda _:None)
 assert module.task_phase_output('t1')=={'phase_key':'srs','generation':3,'status':'waiting','items':[]}

def test_ui_has_no_environment_output_or_banner_selector():
 root=Path(__file__).parents[1]/'src/delivery_api/web'
 assert 'local_preview' not in (root/'phase-output.js').read_text()
 assert 'local_preview' not in (root/'task.js').read_text()
 assert 'previewBannerText' not in (root/'chrome.js').read_text()

def test_missing_reference_artifact_fails_normally_without_location_wording(monkeypatch):
 import delivery_api.app as module
 from fastapi import HTTPException
 monkeypatch.setenv('DELIVERY_LOCAL_PREVIEW','1')
 monkeypatch.setattr(module,'store',SimpleNamespace())
 monkeypatch.setattr(module,'_task_artifact_snapshot',lambda _: [{'phase_key':'srs','name':'srs.json','ref':'/nonexistent-fixture/srs.json'}])
 with pytest.raises(HTTPException) as error:module._task_artifact('fixture','srs','srs.json')
 assert error.value.status_code==404 and error.value.detail=='artifact file not found'
