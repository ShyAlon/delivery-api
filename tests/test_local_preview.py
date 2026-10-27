"""The local preview (DELIVERY_LOCAL_PREVIEW=1): no server copy exists, so every file-backed view says so explicitly."""
import os
from types import SimpleNamespace

import pytest
from fastapi import HTTPException


def test_the_boot_config_and_auth_status_expose_the_local_preview(monkeypatch):
    from fastapi.testclient import TestClient
    import delivery_api.app as module
    c = TestClient(module.app)
    monkeypatch.delenv("DELIVERY_LOCAL_PREVIEW", raising=False)
    assert "local_preview" not in c.get("/api/auth").json()
    monkeypatch.setenv("DELIVERY_LOCAL_PREVIEW", "1")
    assert c.get("/api/auth").json()["local_preview"] is True
    page = c.get("/").text
    assert '"local_preview": true' in page and '"preview": false' in page


def test_a_missing_artifact_file_names_the_local_preview(monkeypatch):
    import delivery_api.app as module
    record = [{"phase_key": "architecture", "name": "architecture.json", "ref": "/nonexistent-local-preview/architecture.json"}]
    monkeypatch.setattr(module, "_task_artifact_snapshot", lambda snapshot_task_id, **kwargs: record)
    monkeypatch.setenv("DELIVERY_LOCAL_PREVIEW", "1")
    with pytest.raises(HTTPException) as local:
        module._task_artifact("t1", "architecture", "architecture.json")
    assert local.value.status_code == 404 and local.value.detail == "Artifact files are not part of a local preview"
    monkeypatch.delenv("DELIVERY_LOCAL_PREVIEW")
    with pytest.raises(HTTPException) as plain:
        module._task_artifact("t1", "architecture", "architecture.json")
    assert plain.value.status_code == 404 and plain.value.detail == "artifact file not found"


def test_phase_output_is_explicitly_unavailable_in_a_local_preview(monkeypatch):
    import delivery_api.app as module
    monkeypatch.setattr(module, "store", SimpleNamespace(task=lambda task_id: {"active_phase": "srs", "generation": 3, "phases": []}))
    monkeypatch.setenv("DELIVERY_LOCAL_PREVIEW", "1")
    result = module.task_phase_output("t1")
    assert result == {"phase_key": "srs", "generation": 3, "status": "local_preview", "items": []}


def test_the_phase_output_note_explains_the_local_preview():
    import json
    import pathlib
    import shutil
    import subprocess
    if shutil.which("node") is None:
        pytest.skip("needs node")
    js = pathlib.Path(__file__).resolve().parents[1] / "src" / "delivery_api" / "web" / "phase-output.js"
    src = r"""
globalThis.window={addEventListener(){}};
const {phaseOutputHtml}=await import(%r);
console.log(JSON.stringify(phaseOutputHtml({phase_key:'srs',generation:3,status:'local_preview',items:[]})));
""" % js.as_uri()
    r = subprocess.run(["node", "--input-type=module", "-e", src], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    html = json.loads(r.stdout)
    assert "not part of a local preview" in html and 'data-status="local_preview"' in html

def test_the_artifacts_zip_download_is_explicitly_unavailable_in_a_local_preview(monkeypatch):
    import delivery_api.app as module
    monkeypatch.setenv("DELIVERY_LOCAL_PREVIEW", "1")
    with pytest.raises(HTTPException) as err:
        module.task_artifacts_zip("t1")
    assert err.value.status_code == 404 and err.value.detail == "Artifact downloads are not part of a local preview"
