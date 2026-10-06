"""Validation panel: server-side grouping (no ids) and the closed-by-default browser rendering."""
import json
from delivery_api.validation_panel import validation_panel

NEEDS={"needs":[{"need_id":"N-1","statement":"Parents can trust the reminders"},
                {"need_id":"N-2","statement":"Kids enjoy the app"},
                {"need_id":"N-3","statement":"Setup is painless"}]}
ITEM={"validation_id":"V-1","need_ids":["N-1"],"criterion_id":"AC-9","components":["api"],
      "what_the_human_does":"Set a reminder and wait","what_to_observe":"The phone buzzes","pass_when":"It buzzes on time",
      "evidence":"Screen recording","environment":"Physical iPhone","verified_by":["T-1","T-2"]}
LEDGER={"findings":{"F-1":{"state":"OPEN","rule_id":"validation.uncovered_need","targets":["N-2"]},
                    "F-2":{"state":"ADDRESSED","rule_id":"validation.uncovered_need","targets":["N-3"]}}}


def test_panel_groups_by_need_and_flags_only_open_uncovered():
    panel = validation_panel({"items": [ITEM]}, NEEDS, [], LEDGER)
    covered, uncovered, closed = panel["groups"]
    assert covered["need"] == "Parents can trust the reminders" and len(covered["items"]) == 1 and not covered["uncovered"]
    assert covered["items"][0]["automatic_checks"] == 2
    assert uncovered["uncovered"] is True and uncovered["items"] == []
    assert closed["uncovered"] is False  # finding already addressed
    assert panel["item_count"] == 1


def test_panel_exposes_no_record_ids():
    text = json.dumps(validation_panel({"items": [ITEM]}, NEEDS, [], LEDGER))
    for raw in ("N-1", "N-2", "V-1", "AC-9", "T-1", "F-1"):
        assert raw not in text


def test_item_for_unknown_need_is_shown_not_dropped():
    stray = {**ITEM, "need_ids": ["N-99"]}
    panel = validation_panel({"items": [stray]}, NEEDS, [], None)
    assert panel["groups"][-1]["need"] == "Other" and len(panel["groups"][-1]["items"]) == 1


def test_missing_artifacts_give_empty_panel():
    assert validation_panel(None, None, None, None) == {"groups": [], "item_count": 0}


def test_older_task_gets_a_pending_validation_phase(tmp_path):
    import sqlite3
    from delivery_api.store import SQLiteStore
    path = str(tmp_path / "old.db")
    store = SQLiteStore(path)
    tid = store.create_task("Old task")["task_id"]
    db = sqlite3.connect(path)
    db.execute("DELETE FROM phases WHERE task_id=? AND phase_key='validation'", (tid,))
    db.execute("DELETE FROM gates WHERE task_id=? AND gate_key='gate-validation'", (tid,))
    db.commit(); db.close()
    reopened = SQLiteStore(path)
    task = reopened.task(tid)
    assert [p["phase_key"] for p in task["phases"]][-1] == "validation"
    assert next(p for p in task["phases"] if p["phase_key"] == "validation")["state"] == "pending"


def test_bundle_reads_validation(tmp_path, monkeypatch):
    from delivery_api import bundles
    spec = tmp_path / "s1" / "spec"; spec.mkdir(parents=True)
    (spec / "validation.json").write_text(json.dumps({"session_id": "s1", "items": [ITEM]}))
    monkeypatch.setenv("VMODEL_ARTIFACTS_DIR", str(tmp_path))
    out = bundles.bundle("s1")
    assert out["phase_status"]["validation"] == "done" and len(out["validation"]) == 1
