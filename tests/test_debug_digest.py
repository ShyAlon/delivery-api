"""The Debug 'Copy diagnostic summary' digest and the explicit everything ZIP download."""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

WEB = Path(__file__).resolve().parents[1] / "src" / "delivery_api" / "web"
needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="needs node")


def _node(source):
    result = subprocess.run(["node", "--input-type=module", "-e", source], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    return result.stdout


def _summary(diag):
    return _node(
        "import {buildDiagnosticSummary} from '%s';console.log(buildDiagnosticSummary(%s))"
        % (WEB / "debug-digest.js", json.dumps({"diag": diag, "runtime": "abc1234", "url": "https://app.example/tasks/c8b0733de7d6?token=leakme", "now": "2026-10-24T18:00:00.000Z"})))


MEDITATOR_DIAG = {
    "task": {"task_id": "c8b0733de7d6", "title": "Meditator", "state": "failed", "active_phase": "intent", "generation": 1},
    "question": None,
    "engine": {"available": False, "error": "engine answered HTTP 404: session not found"},
    "events": {"total": 6, "shown": 4, "items": [
        {"id": 210, "ts": 1792861834.2, "kind": "command", "phase_key": "intent", "message": "Interview starting", "payload": {"session_id": "eab542ae-4636-48c8-868a-43292118b9ef"}},
        {"id": 211, "ts": 1792861839.6, "kind": "model_preflight", "phase_key": "intent",
         "message": "Interview route ollama rejected: engine program failed during start: InsufficientPrivilege", "payload": {"http_status": 503}},
        {"id": 212, "ts": 1792861840.6, "kind": "error", "phase_key": "intent",
         "message": "Interview start failed: engine program failed during start: InsufficientPrivilege", "payload": {"http_status": 503}},
        {"id": 213, "ts": 1792861900.0, "kind": "error", "phase_key": "intent",
         "message": "engine answered HTTP 404: session not found", "payload": {"http_status": 404}},
    ]},
}


@needs_node
def test_summary_distinguishes_the_earliest_failure_from_later_404s():
    text = _summary(MEDITATOR_DIAG)
    assert "== Earliest available failure ==" in text
    earliest = text.split("== Earliest available failure ==\n", 1)[1].split("\n", 1)[0]
    assert "InsufficientPrivilege" in earliest and "session not found" not in earliest
    later = text.split("== Later failure events (2) ==", 1)[1]
    assert "session not found" in later and "InsufficientPrivilege" in later
    assert later.index("InsufficientPrivilege") < later.index("session not found")  # chronological
    assert "does not establish cause" in text
    assert "last 4 of 6 events" in text and "earlier failure may exist" in text
    assert "token=leakme" not in text  # the page URL itself is scrubbed


@needs_node
def test_summary_full_coverage_wording_when_nothing_is_truncated():
    diag = json.loads(json.dumps(MEDITATOR_DIAG))
    diag["events"]["total"] = 4
    text = _summary(diag)
    assert "all 4 recorded events" in text and "may exist" not in text


@needs_node
def test_summary_redacts_secrets_inside_verbatim_event_text():
    diag = json.loads(json.dumps(MEDITATOR_DIAG))
    diag["events"]["items"][1]["message"] = (
        'connect postgresql://postgres:hunter2secret@db.example.com:5432/postgres failed; '
        'Authorization: Bearer abc.def.ghi-123; notify shy@example.com; api_key: sk-live-abcdefghijklmnop1234')
    text = _summary(diag)
    for secret in ("hunter2secret", "abc.def.ghi-123", "shy@example.com", "sk-live-abcdefghijklmnop1234"):
        assert secret not in text, secret
    assert "postgresql://[redacted]@" in text and "Authorization: [redacted]" in text and "[email]" in text and "api_key: [redacted]" in text


@needs_node
def test_summary_tolerates_missing_diagnostics():
    text = _summary(None)
    assert "No failure events in the events available to this export." in text
    assert "Event coverage: unknown; the event history may be incomplete." in text


@needs_node
def test_summary_quotes_events_verbatim_after_scrubbing():
    text = _summary(MEDITATOR_DIAG)
    assert "Interview route ollama rejected: engine program failed during start: InsufficientPrivilege" in text


def test_everything_zip_discloses_omissions_and_downloads_without_the_clipboard():
    ident = (WEB / "identity.js").read_text()
    assert "Known omissions" in ident and "engine process logs" in ident
    assert "downloadTextZip(parts.join" in ident and "'requi-everything.txt'" in ident
    assert "copysummary" in ident and "downloadeverything" in ident and "copyeverything" not in ident
    assert "deliverTextZip(text,'requi-diagnostic-summary.txt')" in ident
    core = (WEB / "client-core.js").read_text()
    fn = re.search(r"export function downloadTextZip\(.*", core).group(0)
    assert "navigator.clipboard" not in fn and "zipText(" in fn and "'.zip'" in fn


def test_every_exported_part_passes_through_the_scrubber():
    ident = (WEB / "identity.js").read_text()
    assert "scrubPart(text)" in ident  # add() scrubs every assembled part, JSON-aware
    digest = (WEB / "debug-digest.js").read_text()
    assert digest.count("scrubText(") >= 5  # header fields and event lines
