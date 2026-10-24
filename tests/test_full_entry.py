"""Hermetic checks of the full-service launcher's decisions. No process is started, no network."""
import importlib.util
from pathlib import Path

SPEC=importlib.util.spec_from_file_location('full_entry',Path(__file__).resolve().parents[1]/'scripts'/'docker'/'full_entry.py')
full_entry=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(full_entry)


def test_refuses_missing_default_and_short_engine_token():
    for token in (None,'','local-interview-engine-token','short'):
        env={} if token is None else {'INTERVIEW_ENGINE_TOKEN':token}
        assert any('INTERVIEW_ENGINE_TOKEN' in p for p in full_entry.check_env(env))


def test_accepts_private_token_and_refuses_control_plane_flag():
    good={'INTERVIEW_ENGINE_TOKEN':'x'*32,'DELIVERY_DATABASE_URL':'postgresql://delivery_app:x@db.example:6543/postgres'}
    assert full_entry.check_env(good)==[]
    assert any('CONTROL_PLANE_ONLY' in p for p in full_entry.check_env({**good,'DELIVERY_CONTROL_PLANE_ONLY':'1'}))


def test_engine_is_loopback_only_and_delivery_runs_as_module():
    engine,delivery=full_entry.commands('python')
    assert engine[engine.index('--host')+1]=='127.0.0.1'
    assert engine[engine.index('--port')+1]=='8010'
    assert delivery==['python','-m','delivery_api']


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
    assert full_entry.check_env(file_opt_in)==[]
    no_choice={'INTERVIEW_ENGINE_TOKEN':'x'*32,'DELIVERY_DATABASE_URL':'sqlite:////tmp/x.db'}
    problems=full_entry.check_env(no_choice)
    assert any('INTERVIEW_ENGINE_SESSIONS_DSN' in p and 'INTERVIEW_ENGINE_SESSIONS_FILE' in p for p in problems),problems
