"""Runtime observability bundle: quiet console, slow-request evidence, image build time, UI action logging wiring, and one-events-read-per task poll.

Hermetic: no network, no Docker, no browser. Node tests skip without node.
"""
import logging
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "src" / "delivery_api" / "web"

needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="needs node")


def _node(source):
    result = subprocess.run(["node", "--input-type=module", "-e", source], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    return result.stdout


# --- quiet console -------------------------------------------------------

def test_routine_activity_is_file_only_when_marked(caplog):
    from delivery_api import observability
    records = []
    class Capture(logging.Handler):
        def emit(self, record):
            if getattr(record, 'console', True):
                records.append(record)
    handler = Capture()
    observability.logger.addHandler(handler)
    try:
        observability.event("http.request", "Request completed", console=False, path='/api/tasks/x', status=200)
        observability.event("http.request", "Request completed", level=logging.WARNING, console=True, path='/api/tasks/x', status=200)
    finally:
        observability.logger.removeHandler(handler)
    assert len(records) == 1 and "Request completed" in records[0].getMessage()


def test_request_log_middleware_quiets_routine_and_warns_on_slow():
    src = (ROOT / "src" / "delivery_api" / "app.py").read_text()
    assert re.search(r"SLOW_REQUEST_MS\s*=\s*1000", src)
    line = re.search(r'activity\("http\.request".*', src).group(0)
    assert "console=_noteworthy" in line and "db_queries=stats.queries" in line and "db_ms=" in line
    assert "_noteworthy=response.status_code>=400 or duration_ms>=SLOW_REQUEST_MS" in src


def test_uvicorn_access_log_disabled():
    src = (ROOT / "src" / "delivery_api" / "__main__.py").read_text()
    assert "access_log=False" in src


# --- image build time -----------------------------------------------------

def test_image_build_time_baked_and_surfaced():
    dockerfile = (ROOT / "Dockerfile.full").read_text()
    assert "ARG REQUI_BUILD_AT=" in dockerfile and "ENV REQUI_BUILD_AT=$REQUI_BUILD_AT" in dockerfile
    for script in ("build-local-image.sh", "build-full-image.sh"):
        text = (ROOT / "scripts" / "docker" / script).read_text()
        assert "--build-arg REQUI_BUILD_AT=" in text and "date -u" in text
    src = (ROOT / "src" / "delivery_api" / "app.py").read_text()
    assert '"built_at":built_at' in src and '"built_at":RUNTIME_VERSION.get("built_at")' in src


# --- one events read per task GET -----------------------------------------

def test_task_get_reads_events_once(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient
    from delivery_api import config
    from delivery_api.app import app
    from delivery_api.store import SQLiteStore
    store = SQLiteStore(str(tmp_path / 'tasks.db'))
    app.state.store = store
    monkeypatch.setattr('delivery_api.app.store', store)
    monkeypatch.setattr(config, 'SESSION_SECRET', 's' * 40)
    monkeypatch.setattr(config, 'COOKIE_SECURE', False)
    # Cloud profile: the polled path his UI takes (no engine session -> no snapshot call).
    monkeypatch.setattr(config, 'CLOUD_TENANTS', True)
    task = store.create_task('Observed task')
    calls = {"n": 0}
    real_events = store.events
    def counting(task_id):
        calls["n"] += 1
        return real_events(task_id)
    monkeypatch.setattr(store, 'events', counting)
    client = TestClient(app)
    reply = client.get(f"/api/tasks/{task['task_id']}")
    assert reply.status_code == 200, reply.text
    assert calls["n"] == 1, f"expected one events read per request, got {calls['n']}"
    assert reply.json()["events"] == []


# --- UI action logging ------------------------------------------------------

def test_ui_action_module_is_preloaded_and_wired():
    index = (WEB / "index.html").read_text()
    assert '/assets/ui-action.js' in index
    checks = {
        "identity.js": ["actionBusy('auth.'+mode", "actionIdle('auth.'+mode", "logEnter('auth.'+mode"],
        "task.js": ["actionBusy('engine.start'", "actionIdle('engine.start'", "guardBlocked('engine.answer'",
                    "disabledSet('engine.answer'", "disabledClear('engine.answer'", "logEnter('answer-composer'"],
        "dialogs.js": ["logEnter('dialog','form.requestSubmit')", "logEnter('dialog','primary.click')", "logEnter('dialog','none"],
    }
    for name, needles in checks.items():
        text = (WEB / name).read_text()
        assert "from './ui-action.js'" in text, f"{name} must import ui-action.js"
        for needle in needles:
            assert needle in text, f"{name} missing {needle}"


@needs_node
def test_ui_action_functions_set_disabled_and_log_names_only():
    src = """
globalThis.location={pathname:'/tasks/x/view'};
globalThis.fetch=()=>Promise.resolve({ok:true});
globalThis.window=globalThis;
globalThis.addEventListener=()=>{};
globalThis.document={querySelector:()=>null,addEventListener:()=>{}};
const lines=[];const orig=console.info;console.info=(...a)=>lines.push(a.map(String).join(' '));
const m=await import('%s');
const button={disabled:false};
m.actionBusy('engine.answer',button);
if(button.disabled!==true)throw new Error('actionBusy did not disable');
m.actionIdle('engine.answer',button,'settled');
if(button.disabled!==false)throw new Error('actionIdle did not re-enable');
m.guardBlocked('engine.answer','already-sending');
m.disabledSet('engine.answer','all-controls');
m.disabledClear('engine.answer','textarea+submit');
m.logEnter('answer-composer','form.requestSubmit');
console.info=orig;
process.stdout.write(JSON.stringify(lines));
""" % (WEB / "ui-action.js")
    import json
    lines = json.loads(_node(src))
    text = "\n".join(lines)
    for needle in ("ui.inflight", "engine.answer busy", "idle (settled)", "blocked: already-sending",
                   "ui.disabled", "ui.enter", "answer-composer: Enter -> form.requestSubmit"):
        assert needle in text, f"missing log line {needle!r} in {text!r}"
