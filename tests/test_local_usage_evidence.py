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


def test_invalid_numeric_usage_is_rejected_instead_of_claiming_complete(tmp_path):
    path=tmp_path/'usage.jsonl'
    path.write_text('\n'.join(json.dumps(row) for row in [
        {'prompt_tokens':'bad'}, {'completion_tokens':-1}, {'prompt_tokens':1.5},
        {'prompt_tokens':True}, {'latency_s':'NaN'}, {'latency_s':-1},
        {'prompt_tokens':3,'completion_tokens':2}]))
    rows=pricing._rows(path)
    assert rows.rejected_rows==6 and rows==[{'prompt_tokens':3,'completion_tokens':2}]
