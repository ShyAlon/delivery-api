"""Owner-script order and refusal, no user DB, provider or Docker effects."""
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
import pytest

ROOT=Path(__file__).resolve().parents[1]


def load(name):
    spec=importlib.util.spec_from_file_location(name,ROOT/'scripts/owner'/name.replace('_','-').__add__('.py'))
    m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m


def fixture(tmp_path,monkeypatch):
    m=load('answer_candidate_test');root=tmp_path/'delivery-api'
    for name in ('delivery-api','interview-engine','spec-generator','vmodel-contracts','verification-loop','vmodel-spec-app'):
        (tmp_path/name/'.git').mkdir(parents=True)
    for name in ('scripts/ops/req','scripts/owner/answer-proof.py','scripts/owner/apply-pg-cutover.py','tests/test_answer_host_integration.py','.venv/bin/python'):
        p=root/name;p.parent.mkdir(parents=True,exist_ok=True);p.touch()
    (root/'.env').write_text('fake secret never printed');(root/'.env').chmod(0o600)
    monkeypatch.setattr(m,'module',lambda path:SimpleNamespace(read_file=lambda p:{'DELIVERY_DATABASE_URL':'test-only-dsn','REQUI_ADMIN_DATABASE_URL':'test-only-admin-dsn'}))
    monkeypatch.setattr(m,'git',lambda root,*args:'main' if args[0]=='branch' else 'https://github.com/fake/test.git' if args[0]=='remote' else '')
    monkeypatch.setattr(m,'database_preflight',lambda dsn:None)
    monkeypatch.setattr(m.subprocess,'run',lambda *a,**kw:SimpleNamespace(returncode=0))
    monkeypatch.setattr(Path,'home',classmethod(lambda cls:tmp_path/'home'))
    return m,root


@pytest.mark.parametrize('pending',[0,3])
def test_owner_sequence_applies_only_pending_and_always_collects_proof(tmp_path,monkeypatch,pending):
    m,root=fixture(tmp_path,monkeypatch);calls=[];checked=[False]
    def run(command,**kwargs):
        calls.append((list(map(str,command)),kwargs))
        if '--check' in command and not checked[0]:checked[0]=True;return pending
        return 0
    monkeypatch.setattr(m,'run',run)
    assert m.main([str(root)])==0
    cmds=[c for c,kw in calls]
    assert sum('apply-pg-cutover.py' in ' '.join(c) and '--apply' in c for c in cmds)==(pending==3)
    assert any(c[-2:]==['rebuild','--apply'] for c in cmds)
    assert 'answer-proof.py' in ' '.join(cmds[-2])
    assert cmds[-1][-3:-1]==['debug','--run-dir']
    for c,kw in calls:
        if c[0]=='uv':assert 'DELIVERY_DATABASE_URL' not in kw['env'] and 'PREVIEW_SETTINGS_FILE' not in kw['env']
        else:assert kw['env']['PREVIEW_SETTINGS_FILE']==str(root/'.env')


def test_unreachable_db_fails_before_git_or_any_effect(tmp_path,monkeypatch,capsys):
    m,root=fixture(tmp_path,monkeypatch)
    monkeypatch.setattr(m,'database_preflight',lambda dsn:(_ for _ in ()).throw(m.Refused('DB unreachable; no schema or rebuild')))
    monkeypatch.setattr(m,'git',lambda *a:pytest.fail('git must follow network preflight'))
    monkeypatch.setattr(m,'run',lambda *a,**kw:pytest.fail('no effect allowed'))
    with pytest.raises(m.Refused,match='DB unreachable'):m.main([str(root)])
    assert 'fake secret' not in capsys.readouterr().out


def test_db_port_block_has_bounded_clear_refusal(monkeypatch):
    m=load('answer_candidate_test')
    monkeypatch.setattr(m.socket,'create_connection',lambda *a,**kw:(_ for _ in ()).throw(TimeoutError()))
    with pytest.raises(m.Refused,match='University WiFi may block Supabase DB ports'):
        m.database_preflight('host=fake.invalid port=5432 dbname=fixture user=test')


def test_free_proof_sanitizes_environment_and_rejects_missing_counts(tmp_path,monkeypatch):
    m=load('answer_proof');root=tmp_path/'delivery-api';engine=tmp_path/'interview-engine/src/interview_engine'
    engine.mkdir(parents=True);(engine/'pg_checkpoint.py').touch();root.mkdir()
    monkeypatch.setenv('DELIVERY_DATABASE_URL','never-in-test');monkeypatch.setenv('DEEPSEEK_API_KEY','never-in-test')
    def result(command,**kw):
        assert 'DELIVERY_DATABASE_URL' not in kw['env'] and 'DEEPSEEK_API_KEY' not in kw['env']
        assert kw['env']['DELIVERY_TEST_ENGINE_SOURCE']==str(engine.parents[1])
        return SimpleNamespace(returncode=0,stdout='ANSWER_TOTAL success 29 12\n')
    monkeypatch.setattr(m.subprocess,'run',result)
    assert m.main([str(root),'--output',str(tmp_path/'proof')])==1
    value=json.loads((tmp_path/'proof/answer-proof.json').read_text())
    assert not value['passed'] and value['live_provider_calls']==0
