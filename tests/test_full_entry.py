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
    good={'INTERVIEW_ENGINE_TOKEN':'x'*32}
    assert full_entry.check_env(good)==[]
    assert any('CONTROL_PLANE_ONLY' in p for p in full_entry.check_env({**good,'DELIVERY_CONTROL_PLANE_ONLY':'1'}))


def test_engine_is_loopback_only_and_delivery_runs_as_module():
    engine,delivery=full_entry.commands('python')
    assert engine[engine.index('--host')+1]=='127.0.0.1'
    assert engine[engine.index('--port')+1]=='8010'
    assert delivery==['python','-m','delivery_api']
