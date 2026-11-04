"""The text-to-ZIP helpers behind the Debug exports, the unavailable-session wording, and the removed All sessions link."""
import json
import re
import shutil
import subprocess
import zipfile
from pathlib import Path

import pytest

WEB = Path(__file__).resolve().parents[1] / "src" / "delivery_api" / "web"
needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="needs node")


def _node(source):
    result = subprocess.run(["node", "--input-type=module", "-e", source], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    return result.stdout


@needs_node
@pytest.mark.parametrize("text", ["", "one line", "héllo\nwörld ✓ \u2603 " * 700, "x" * 300_000], ids=["empty", "one-line", "unicode", "large"])
def test_the_fallback_zip_unzips_to_exactly_the_text(tmp_path, text):
    out = tmp_path / "z.zip"
    (tmp_path / "in.txt").write_text(text, encoding="utf-8")
    _node("import {zipText} from '%s';import fs from 'fs';fs.writeFileSync('%s',zipText('requi-everything.txt',fs.readFileSync('%s','utf8')))" % (WEB / "zip.js", out, tmp_path / "in.txt"))
    with zipfile.ZipFile(out) as z:
        assert z.testzip() is None and z.namelist() == ["requi-everything.txt"]
        assert z.read("requi-everything.txt") == text.encode("utf-8")
        assert z.getinfo("requi-everything.txt").compress_type == zipfile.ZIP_STORED
    if shutil.which("unzip"):
        assert subprocess.run(["unzip", "-tq", str(out)], capture_output=True).returncode == 0


def test_copy_helpers_try_the_clipboard_first_and_fall_back_to_a_zip():
    core = (WEB / "client-core.js").read_text()
    fn = re.search(r"export async function deliverTextZip\(.*", core).group(0).split("\n")[0]
    assert fn.index("navigator.clipboard.writeText") < fn.index("zipText(") and "application/zip" in fn and "'.zip'" in fn
    ident = (WEB / "identity.js").read_text()
    assert "deliverText(parts.join" not in ident
    assert "requi-everything.zip" in ident
    assert "deliverText(text,'requi-text.txt')" in (WEB / "copy-blocks.js").read_text()   # other copy buttons are unchanged


def _un(control):
    out = _node("import {sessionUnavailable} from '%s';console.log(JSON.stringify(sessionUnavailable(%s)))" % (WEB / "session-unavailable.js", json.dumps(control)))
    return json.loads(out)


def test_only_the_exact_failed_no_process_session_not_found_snapshot_counts_as_unavailable():
    base = {"mode": "failed", "process_live": False, "diagnostic": {"http_status": 404, "code": 120, "detail": "session not found"}}
    assert _un(base) is True
    assert _un({**base, "process_live": True}) is False
    assert _un({**base, "mode": "working"}) is False and _un({**base, "mode": "waiting_input"}) is False
    assert _un({**base, "diagnostic": {"http_status": 404, "code": 7}}) is False
    assert _un({**base, "diagnostic": {"http_status": 500, "code": 120}}) is False
    assert _un({**base, "diagnostic": {"reason": "engine transport unavailable"}}) is False
    assert _un(None) is False and _un({}) is False


def _text(events):
    return _node("import {lastRecorded} from '%s';console.log(lastRecorded(%s))" % (WEB / "session-unavailable.js", json.dumps(events))).strip()


def test_the_unavailable_wording_states_only_what_the_snapshot_shows():
    waiting = _text([{"kind": "progress", "ts": 1792395000}, {"kind": "waiting_input", "ts": 1792395508}])
    assert waiting.startswith("Last recorded state: waiting for your answer at ") and "session unavailable in this environment" in waiting
    other = _text([{"kind": "progress", "ts": 1792395000}])
    assert other.startswith("Session unavailable in this environment; no active process reported; last recorded progress ")
    none = _text([])
    assert "no progress is recorded" in none
    for text in (waiting, other, none):
        low = text.lower()
        for banned in ("earlier stages", "cannot resume", "lost", "nothing running", "rerun", "paid", "credit", "$"):
            assert banned not in low, (banned, text)


def test_the_task_page_uses_the_unavailable_wording_and_stops_the_live_counters():
    task = (WEB / "task.js").read_text()
    assert "sessionUnavailable(t.control_snapshot)" in task and "unavail?'Needs attention':stateLabel(t.state)" in task
    assert task.count("unavailable:unavail?unavailText:''") == 6 and "Session unavailable in this environment" in task
    progress = (WEB / "progress.js").read_text()
    assert "if(unavailable)return `<details class=\"review-progress\"" in progress and progress.index("if(unavailable)")<progress.index("Connected to progress")
    assert "${unavail?'':progressScope(" in task and "${unavail?'Needs attention':stateLabel(t.state)}" in task
    assert "Rerun stage" in task          # the stage rerun button and its existing wording are untouched


def test_no_page_links_to_all_sessions_any_more():
    for path in WEB.glob("*"):
        if path.suffix in (".js", ".html", ".css"):
            assert "All sessions" not in path.read_text(encoding="utf-8"), path.name
    import delivery_api.app as module
    from fastapi.testclient import TestClient
    client = TestClient(module.app)
    for route in ("/", "/about", "/credits", "/ops"):
        body = client.get(route, headers={"Accept": "text/html"}).text
        assert "All sessions" not in body and 'href="/sessions"' not in body, route


def _case_the_old_session_pages_send_people_home(path):
    import delivery_api.app as module
    from fastapi.testclient import TestClient
    response = TestClient(module.app, follow_redirects=False).get(path, headers={"Accept": "text/html"})
    assert response.status_code == 302 and response.headers["location"] == "/", path


def test_the_old_session_page_code_and_host_storage_setting_are_gone():
    assert not (WEB / "sessions.js").exists() and not (WEB / "session-presenters.js").exists()
    for name in ("app.js", "identity.js", "session-cost.js"):
        text = (WEB / name).read_text()
        assert "sessionsDashboard" not in text and "bundleView" not in text and "/sessions/" not in text and "'/sessions'" not in text, name
    import delivery_api.app as module
    paths = {r.path for r in module.app.routes}
    assert "/api/sessions" in paths and "/api/settings/artifacts-root" not in paths


def test_server_boot_config_does_not_carry_a_runtime_mode(monkeypatch):
    import delivery_api.app as module
    monkeypatch.setenv('DELIVERY_PREVIEW','1')
    monkeypatch.setenv('DELIVERY_CLOUD_EXECUTION','1')
    body=module._page('index.html').body.decode()
    config=json.loads(body.split('window.__REQUI_CONFIG__=',1)[1].split(';',1)[0])
    assert config=={}
    monkeypatch.setenv('DELIVERY_CLOUD_EXECUTION','0')
    body=module._page('index.html').body.decode()
    config=json.loads(body.split('window.__REQUI_CONFIG__=',1)[1].split(';',1)[0])
    assert config=={}


@pytest.mark.parametrize("path", ["/sessions", "/sessions/abc", "/sessions/abc/view", "/sessions/abc/needs", "/sessions/abc/interview"])
def test_the_old_session_pages_send_people_home(path,monkeypatch,pg_scoped_workspace,pg_task_app):
    from delivery_api import app as module
    client=TestClient(pg_task_app(pg_scoped_workspace),headers=pg_scoped_workspace.headers,follow_redirects=False)
    response=client.get(path,headers={'Accept':'text/html'})
    assert response.status_code==302 and response.headers['location']=='/'
