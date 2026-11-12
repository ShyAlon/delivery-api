"""Hermetic checks of the full-service launcher's decisions. No process is started, no network."""
import importlib.util
from pathlib import Path

SPEC=importlib.util.spec_from_file_location('full_entry',Path(__file__).resolve().parents[1]/'scripts'/'docker'/'full_entry.py')
full_entry=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(full_entry)


def test_refuses_missing_default_and_short_engine_token():
    for token in (None,'','local-interview-engine-token','short'):
        env={} if token is None else {'INTERVIEW_ENGINE_TOKEN':token}
        assert any('INTERVIEW_ENGINE_TOKEN' in p for p in full_entry.check_env(env))


def test_retired_profile_does_not_select_a_different_launcher():
    good={'INTERVIEW_ENGINE_TOKEN':'x'*32,'DELIVERY_DATABASE_URL':'postgresql://delivery_app:x@db.example:6543/postgres'}
    assert full_entry.check_env(good)==[]
    assert full_entry.check_env({**good,'DELIVERY_CONTROL_PLANE_ONLY':'1'})==[]


def test_delivery_parent_is_the_only_resident_service():
    assert full_entry.command('python')==['python','-m','delivery_api']


def test_engine_session_dsn_is_derived_from_the_delivery_postgres():
    env={'DELIVERY_DATABASE_URL':'postgresql://delivery_app:x@db.example:6543/postgres'}
    assert full_entry.engine_env(env)['INTERVIEW_ENGINE_SESSIONS_DSN']==env['DELIVERY_DATABASE_URL']
    explicit={'INTERVIEW_ENGINE_SESSIONS_DSN':'postgresql://other/host/db','DELIVERY_DATABASE_URL':env['DELIVERY_DATABASE_URL']}
    assert full_entry.engine_env(explicit)['INTERVIEW_ENGINE_SESSIONS_DSN']=='postgresql://other/host/db'
    sqlite={'DELIVERY_DATABASE_URL':'sqlite:////tmp/x.db'}
    assert 'INTERVIEW_ENGINE_SESSIONS_DSN' not in full_entry.engine_env(sqlite)


def test_session_persistence_must_be_chosen_or_startup_refuses():
    good={'INTERVIEW_ENGINE_TOKEN':'x'*32,'DELIVERY_DATABASE_URL':'postgresql://delivery_app:x@db.example:6543/postgres'}
    assert full_entry.check_env(good)==[]
    file_opt_in={'INTERVIEW_ENGINE_TOKEN':'x'*32,'DELIVERY_DATABASE_URL':'sqlite:////tmp/x.db','INTERVIEW_ENGINE_SESSIONS_FILE':'1'}
    assert any('PostgreSQL' in p for p in full_entry.check_env(file_opt_in))
    no_choice={'INTERVIEW_ENGINE_TOKEN':'x'*32,'DELIVERY_DATABASE_URL':'sqlite:////tmp/x.db'}
    problems=full_entry.check_env(no_choice)
    assert any('INTERVIEW_ENGINE_SESSIONS_DSN' in p and 'INTERVIEW_ENGINE_SESSIONS_FILE' in p for p in problems),problems


def test_no_compatibility_engine_launch_branch():
    source=Path(full_entry.__file__).read_text()
    assert 'compatibility_engine_required' not in source
    assert 'interview_engine.http_service:app' not in source


def test_supervisor_releases_report_pool_even_when_preflight_refuses(monkeypatch):
    from types import SimpleNamespace
    from delivery_api import db_pool
    calls=[]
    monkeypatch.setattr(db_pool,'PROCESS_POOL_MAX',9)
    report=SimpleNamespace(generated_at='fixture',refused=True,to_dict=lambda:{'status':'fail','checks':[]},render_text=lambda:'fixture report')
    monkeypatch.setattr(full_entry,'check_env',lambda env:[])
    monkeypatch.setattr(full_entry.preflight,'run',lambda env:report)
    monkeypatch.setattr(full_entry.preflight,'write_report',lambda *a:calls.append('save') or True)
    monkeypatch.setattr(db_pool,'close_all',lambda:calls.append('close'))
    assert full_entry.main()==2
    assert calls==['save']
    assert db_pool.PROCESS_POOL_MAX==9
