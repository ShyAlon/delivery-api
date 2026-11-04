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




def test_bundle_reads_validation(tmp_path, monkeypatch,pg_scoped_workspace):
    from delivery_api import bundles
    store=pg_scoped_workspace.store
    tid=store.create_task('Validation')['task_id'];store.set_engine_session(tid,'validation-test')
    store.publish_task_revision_bytes(tid,[{'phase_key':'validation','name':'validation.json',
        'data':json.dumps({'session_id':'validation-test','items':[ITEM]}).encode()}],writer_instance='test-only')
    monkeypatch.setenv('VMODEL_ARTIFACTS_DIR',str(tmp_path/'host-missing'))
    out=bundles.postgres_bundle(store,'validation-test')
    assert out['phase_status']['validation']=='done' and len(out['validation'])==1
