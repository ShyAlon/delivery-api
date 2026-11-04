"""Retained usage evidence is PG-only and survives invalid source rows intact."""
import pytest
from delivery_api.generator_documents import GeneratorDocuments
from delivery_api.pg_usage import usage_rows,update_rollup


@pytest.fixture
def evidence(pg_scoped_workspace):
    store=pg_scoped_workspace.store;tid=store.create_task('Usage evidence')['task_id']
    store.set_engine_session(tid,'evidence-'+__import__('uuid').uuid4().hex)
    job=store.enqueue_worker(tid,'phase:needs')
    docs=GeneratorDocuments(store,store.claim_worker(job),job)
    return store,tid,docs


def test_bad_json_and_non_objects_are_counted_without_rewriting_evidence(evidence):
    store,tid,docs=evidence;raw=b'{"prompt_tokens":3}\nnot-json\n[]\n'
    docs.write('interview/usage.jsonl',raw)
    rows,summary=usage_rows(store,tid)
    assert rows==[[{'prompt_tokens':3}],[]]
    assert summary['rejected_rows']==2 and not summary['complete']
    assert docs.read('interview/usage.jsonl')==raw


def test_missing_first_run_usage_is_empty_not_corrupt(evidence):
    store,tid,docs=evidence;rows,summary=usage_rows(store,tid)
    assert rows==[[],[]] and summary['complete'] and summary['rejected_rows']==0


def test_rollup_marks_incomplete_even_when_no_valid_rows(evidence):
    import json
    store,tid,docs=evidence;docs.write('interview/usage.jsonl',b'bad\n')
    summary=update_rollup(store,tid)
    assert summary['complete'] is False and summary['rejected_rows']==1
    assert json.loads(docs.read('cost.json'))['evidence']==summary
    assert docs.read('interview/usage.jsonl')==b'bad\n'


def test_numeric_evidence_status_does_not_rewrite_source(evidence):
    store,tid,docs=evidence;raw=b'{"prompt_tokens":"bad"}\n'
    docs.write('interview/usage.jsonl',raw);rows,summary=usage_rows(store,tid)
    assert rows==[[{'prompt_tokens':'bad'}],[]]
    assert not summary['complete'] and summary['invalid_numeric_rows']==1
    assert docs.read('interview/usage.jsonl')==raw


def test_cost_endpoint_does_not_compute_unknown_numeric_total(evidence,pg_task_app,pg_scoped_workspace,monkeypatch):
    from fastapi.testclient import TestClient
    from delivery_api import app as module
    store,tid,docs=evidence;docs.write('interview/usage.jsonl',b'{"prompt_tokens":"bad"}')
    monkeypatch.setattr(module,'_task_cost',lambda task: (_ for _ in ()).throw(AssertionError('must not price unknown evidence')))
    response=TestClient(pg_task_app(pg_scoped_workspace),headers=pg_scoped_workspace.headers).get(f'/api/tasks/{tid}/cost')
    assert response.status_code==200,response.text
    assert response.json()['price'] is None and not response.json()['evidence']['complete']
