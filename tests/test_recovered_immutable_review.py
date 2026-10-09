import pytest
from delivery_api.store import SQLiteStore


def test_retained_review_exact_replay_is_idempotent_conflicting_revision_rejected(tmp_path):
    s=SQLiteStore(str(tmp_path/'immutable.db'));tid=s.create_task('immutable')['task_id']
    payload={'task':s.task(tid),'documents':[{'name':'needs.json','view':{'content':'original'}}]}
    s.retain_decision_review(tid,'gate:gate-needs','same',payload)
    s.retain_decision_review(tid,'gate:gate-needs','same',payload)
    changed={**payload,'documents':[{'name':'needs.json','view':{'content':'changed'}}]}
    with pytest.raises(ValueError,match='conflicting'):
        s.retain_decision_review(tid,'gate:gate-needs','same',changed)
    assert s.decision_review(tid,'gate:gate-needs','same')==payload
    s.retain_decision_review(tid,'gate:gate-needs','new',changed)
    assert s.decision_review(tid,'gate:gate-needs','new')==changed
