import json
from delivery_api import pricing


def test_bad_json_and_non_objects_are_counted_without_rewriting_evidence(tmp_path, caplog):
    path=tmp_path/'usage.jsonl';original='{"prompt_tokens":3}\nnot-json\n[]\n'
    path.write_text(original)
    rows=pricing._rows(path)
    assert rows == [{'prompt_tokens':3}]
    assert rows.rejected_rows == 2 and not rows.complete
    assert path.read_text() == original
    assert 'local_usage_incomplete' in caplog.text and 'not-json' not in caplog.text


def test_missing_first_run_usage_is_empty_not_corrupt(tmp_path):
    rows=pricing._rows(tmp_path/'absent')
    assert rows == [] and rows.complete and rows.rejected_rows == 0


def test_rollup_marks_incomplete_even_when_no_valid_rows(tmp_path):
    (tmp_path/'interview').mkdir();(tmp_path/'interview/usage.jsonl').write_text('bad\n')
    report=pricing.write_cost_json(tmp_path)
    assert report['evidence']['complete'] is False
    assert report['evidence']['rejected_rows'] == 1
    assert json.loads((tmp_path/'cost.json').read_text())['evidence'] == report['evidence']


def test_numeric_evidence_status_does_not_change_pricing_rows(tmp_path):
    root=tmp_path/'interview';root.mkdir();path=root/'usage.jsonl'
    path.write_text('{"prompt_tokens":"bad"}\n')
    assert pricing._rows(path)==[{'prompt_tokens':'bad'}]
    assert pricing.usage_evidence(tmp_path)['complete'] is False
    assert pricing.usage_evidence(tmp_path)['invalid_numeric_rows']==1


def test_cost_endpoint_does_not_compute_unknown_numeric_total(tmp_path,monkeypatch):
    from delivery_api import app as module
    from types import SimpleNamespace
    monkeypatch.setattr(module.config,'CLOUD_TENANTS',False)
    monkeypatch.setattr(module,'store',SimpleNamespace(task=lambda task:True))
    monkeypatch.setattr(module,'_recorded_engine_session',lambda task:'session')
    monkeypatch.setattr(module,'authorized_session',lambda *args:tmp_path)
    monkeypatch.setattr(module,'_task_cost',lambda task: (_ for _ in ()).throw(AssertionError('must not price unknown evidence')))
    (tmp_path/'interview').mkdir();(tmp_path/'interview/usage.jsonl').write_text('{"prompt_tokens":"bad"}')
    result=module.task_cost('task',None)
    assert result['price'] is None and result['evidence']['complete'] is False
