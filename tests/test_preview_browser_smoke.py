"""The exec preview's step-9 browser smoke vs a REAL anonymous boot.

2026-10-27: the preview aborted at step 9 (BROWSER-SMOKE) on a HEALTHY page. The
gate failed on any console error, but an anonymous cloud boot always logs the
page's own pre-sign-in probes as console errors (the tenant boundary 401s them).
The hermetic boot test (test_ui_playwright.py) runs in local auth mode, where
those probes succeed - so the real anonymous 401 behavior was never exercised.

These tests boot the REAL page + REAL TenantMiddleware (cloud needs Postgres;
for a token-less anonymous visitor the middleware answers 401 before any
verifier or database call, so wrapping the sqlite-mode app reproduces exactly
what an anonymous browser meets on the live preview).
"""
import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit

import pytest

pytest.importorskip("playwright.sync_api")

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts" / "ops"))
import browser_smoke

WRAPPER = '''from delivery_api.app import app
from delivery_api.tenant_http import TenantMiddleware

class _Verifier:
    def verify_with_email(self, token): raise AssertionError("no tokens in the anonymous harness")
    def verify_with_operator(self, token): raise AssertionError("no tokens in the anonymous harness")
    def verify(self, token): raise AssertionError("no tokens in the anonymous harness")

wrapped = TenantMiddleware(app, verifier=_Verifier(), session_check=lambda session_id: False)
'''


def _port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def anon_app(tmp_path_factory):
    root = tmp_path_factory.mktemp("anon")
    (root / "wrapped_app.py").write_text(WRAPPER)
    env = {**os.environ,
           "DELIVERY_DATABASE_URL": f"sqlite:///{root}/anon.db",
           "VMODEL_ARTIFACTS_DIR": str(root / "artifacts"),
           "PYTHONPATH": f"{REPO / 'src'}:{root}"}
    port = _port()
    proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "wrapped_app:wrapped",
                             "--host", "127.0.0.1", "--port", str(port)], env=env)
    base = f"http://127.0.0.1:{port}"
    for _ in range(80):
        try:
            import urllib.request
            urllib.request.urlopen(base + "/health", timeout=.2)
            break
        except Exception:
            time.sleep(.1)
    else:
        raise RuntimeError("anonymous harness app did not start")
    yield base
    proc.terminate()
    proc.wait(timeout=5)


def _events(responses=(), console=(), pageerrors=(), failures_=(), boot="ok"):
    return {"responses": list(responses), "console": list(console),
            "pageerrors": list(pageerrors), "request_failures": list(failures_), "boot": boot}


def test_a_real_anonymous_boot_tolerates_only_the_named_expected_401s(anon_app):
    events = browser_smoke.collect(anon_app)
    assert events is not None, "chromium missing: .venv/bin/python -m playwright install chromium"
    assert events["boot"] == "ok", f"the healthy page must boot: {events['pageerrors']}"
    # The reproduction is real only if the boot really met the anonymous 401s
    # and their console echoes - the exact conditions that aborted Shy's preview:
    probed = {(r["method"], urlsplit(r["url"]).path) for r in events["responses"] if r["status"] == 401}
    assert probed == browser_smoke.EXPECTED_ANONYMOUS_401, "the page's anonymous probes changed - review the expected set"
    assert any(c["text"].startswith("Failed to load resource") for c in events["console"]), \
        "under the old any-console-error gate this boot FAILS - the false positive this test pins"
    failures, tolerated = browser_smoke.classify(events)
    assert failures == [], failures
    assert events["telemetry_success"], "anonymous client-log POST must succeed"
    named = " ".join(tolerated)
    for _, path in browser_smoke.EXPECTED_ANONYMOUS_401:
        assert path in named, "every tolerated probe must be named in the output"


def test_an_unexpected_401_fails_and_names_the_url():
    events = _events(responses=[{"method": "POST", "url": "http://x/api/admin/operations/stop-run", "status": 401}])
    failures, _ = browser_smoke.classify(events)
    assert failures == ["HTTP 401 POST http://x/api/admin/operations/stop-run"]


def test_an_expected_path_with_another_status_fails():
    events = _events(responses=[{"method": "POST", "url": "http://x/api/client-log", "status": 500}])
    failures, _ = browser_smoke.classify(events)
    assert failures == ["HTTP 500 POST http://x/api/client-log"]


def test_every_expected_anonymous_401_passes():
    events = _events(responses=[{"method": m, "url": "http://x" + p, "status": 401}
                                for m, p in browser_smoke.EXPECTED_ANONYMOUS_401])
    failures, tolerated = browser_smoke.classify(events)
    assert failures == [] and len(tolerated) == len(browser_smoke.EXPECTED_ANONYMOUS_401)


def test_a_resource_load_console_line_is_dropped_only_when_a_response_explains_it():
    with_response = _events(responses=[{"method": "GET", "url": "http://x/api/tasks", "status": 401}],
                            console=[{"text": "Failed to load resource: the server responded with a status of 401 (Unauthorized)", "detail": None}])
    assert browser_smoke.classify(with_response)[0] == []
    orphan = _events(console=[{"text": "Failed to load resource: the server responded with a status of 404 (Not Found)", "detail": None}])
    failures, _ = browser_smoke.classify(orphan)
    assert len(failures) == 1 and "no captured response explains" in failures[0]


def test_the_pages_own_telemetry_echo_is_tolerated_only_for_an_expected_endpoint():
    good = _events(console=[{"text": "[Requi.re t] api.error: Error: Supabase sign-in required",
                             "detail": {"endpoint": "/api/tasks", "http_status": 401}}])
    assert browser_smoke.classify(good)[0] == []
    bad = _events(console=[{"text": "[Requi.re t] api.error: Error: boom",
                            "detail": {"endpoint": "/api/sessions/abc", "http_status": 401}}])
    assert browser_smoke.classify(bad)[0] != []
    undetailed = _events(console=[{"text": "[Requi.re t] api.error: Error: Supabase sign-in required", "detail": None}])
    assert browser_smoke.classify(undetailed)[0] != [], "an echo without its structured endpoint cannot be accounted"


def test_every_other_browser_error_fails():
    assert browser_smoke.classify(_events(console=[{"text": "TypeError: x is not a function", "detail": None}]))[0] != []
    assert browser_smoke.classify(_events(pageerrors=["SyntaxError: broken"]))[0] != []
    failures, _ = browser_smoke.classify(_events(failures_=[{"method": "GET", "url": "http://x/assets/app.js", "failure": "net::ERR_ABORTED"}]))
    assert failures == ["request failed: GET http://x/assets/app.js - net::ERR_ABORTED"]
    assert browser_smoke.classify(_events(boot="stuck"))[0] != []
    assert browser_smoke.classify(_events(boot="empty"))[0] != []


def test_the_launcher_delegates_to_the_smoke_module():
    """The classification logic must be the tested module, not an untestable
    heredoc: preview-exec.sh calls scripts/ops/browser_smoke.py and keeps the
    BROWSER-SMOKE abort and the chromium-missing note."""
    src = (REPO / "scripts" / "ops" / "preview-exec.sh").read_text()
    assert '.venv/bin/python scripts/ops/browser_smoke.py "http://127.0.0.1:$PORT"' in src
    assert "abort BROWSER-SMOKE" in src and "playwright install chromium" in src
    assert "page.on(" not in src, "the inline playwright heredoc is gone - the logic lives in the tested module"


def test_telemetry_401_is_not_an_expected_anonymous_error():
    events = _events(responses=[{"method":"POST","url":"http://x/api/client-log","status":401}])
    assert browser_smoke.classify(events)[0] == ["HTTP 401 POST http://x/api/client-log"]


def test_missing_successful_telemetry_fails_collected_boot():
    events = _events()
    events["telemetry_success"] = False
    assert browser_smoke.classify(events)[0] == ["no successful anonymous POST /api/client-log captured"]
