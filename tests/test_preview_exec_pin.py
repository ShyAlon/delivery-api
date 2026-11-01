"""Legacy pin entrypoint reads reviewed shared-store authority, never vendor pages."""
import runpy
from pathlib import Path
import pytest
from delivery_api import model_pins
ROOT=Path(__file__).resolve().parents[1]


def run_pin(tmp_path,monkeypatch,select):
    import sys
    from delivery_api import postgres_scope
    monkeypatch.setattr(postgres_scope,'ScopedPostgres',lambda dsn:object())
    monkeypatch.setenv('DELIVERY_DATABASE_URL','dbname=fixture_no_connect')
    monkeypatch.setattr(model_pins,'selected_environment',select)
    monkeypatch.setattr(sys,'argv',['pin.py',str(tmp_path)])
    runpy.run_path(str(ROOT/'scripts/ops/pin.py'),run_name='__main__')


def test_compatibility_pin_uses_only_shared_store(tmp_path,monkeypatch):
    from delivery_api import vendor_sources
    monkeypatch.setattr(vendor_sources,'fetch_verified_source',lambda u:pytest.fail('compatibility pin must not independently fetch vendor pages'))
    seen=[]
    def select(db,environment):
        seen.append(environment['DELIVERY_DATABASE_URL'])
        return {'VMODEL_REVIEWED_MODEL_PRICES':'private reviewed content'}
    run_pin(tmp_path,monkeypatch,select)
    assert seen==['dbname=fixture_no_connect']
    assert (tmp_path/'cloud-models.env').read_text()=='REQUI_PIN_STORE=1\nREQUI_PIN_PROVIDERS=deepseek\n'


def test_missing_or_changed_review_fails_closed_without_output(tmp_path,monkeypatch):
    def select(*a):raise ValueError('No reviewed default model')
    with pytest.raises(SystemExit,match='PIN-FAIL.*No reviewed default model'):
        run_pin(tmp_path,monkeypatch,select)
    assert not (tmp_path/'cloud-models.env').exists()


def test_provider_selection_is_preserved_not_invented(tmp_path,monkeypatch):
    monkeypatch.setenv('REQUI_PIN_PROVIDERS','ollama')
    run_pin(tmp_path,monkeypatch,lambda *a:{})
    assert (tmp_path/'cloud-models.env').read_text()=='REQUI_PIN_STORE=1\nREQUI_PIN_PROVIDERS=ollama\n'
