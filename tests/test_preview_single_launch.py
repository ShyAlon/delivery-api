"""One launcher, explicit test scope, fail closed on route identity mismatch."""
from pathlib import Path
from types import SimpleNamespace
import asyncio
import pytest
from fastapi import HTTPException
ROOT=Path(__file__).parents[1]


def test_legacy_initial_model_must_match_reviewed_gateway(monkeypatch):
    from delivery_api import app as m,cloud_runtime
    monkeypatch.setattr(m.config,'CLOUD_TENANTS',True)
    monkeypatch.setattr(m,'store',SimpleNamespace(task=lambda tid:{'state':'not_started','brief':'test'}))
    monkeypatch.setattr(cloud_runtime,'gateway_factory',lambda:SimpleNamespace(models={'ollama':'phi4'}))
    monkeypatch.setenv('DELIVERY_LOCAL_PREVIEW','1')
    monkeypatch.setattr(m.engine_client,'default_route',lambda:{'route':'ollama','model':'qwen2.5-coder:14b'})
    with pytest.raises(HTTPException) as exc:
        asyncio.run(m.start_task_engine('fake',m.TaskStartRequest(model_route='ollama',initial_model='qwen2.5-coder:14b'),a=None))
    assert exc.value.status_code==422
    assert 'reviewed ollama:phi4' in exc.value.detail['cause']


def test_single_entry_flags_and_identity_before_optional_skips():
    owner=(ROOT/'scripts/ops/req-owner.sh').read_text()
    assert 'preview|preview-free)' in owner
    assert '--fast retired; exact-image acceptance is never skipped.' in owner
    preview=(ROOT/'scripts/docker/local_image_preview.py').read_text()
    assert preview.index("inspect_image(image_id)['Id']")<preview.index("command(['docker',*args])")
    assert 'SKIPPED' not in preview
