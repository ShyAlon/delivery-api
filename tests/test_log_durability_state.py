import logging
from delivery_api import observability, engine_log
from delivery_api.postgres_logs import flush


def test_activity_pg_failure_warns_once_and_is_operator_visible(tmp_path, monkeypatch, capsys):
    from delivery_api import log_durability as state
    monkeypatch.setattr(state, '_failures', {})
    handler = observability._EvidenceFileHandler(tmp_path/'activity.log', delay=True)
    from delivery_api import postgres_logs
    def broken(*args,**kwargs):raise OSError('fake-secret')
    monkeypatch.setattr(postgres_logs.PostgresLogs,'append',broken)
    monkeypatch.setattr(handler, 'stream', None)
    monkeypatch.setattr(handler, '_open', lambda: (_ for _ in ()).throw(OSError('fake-secret')))
    for _ in range(3): handler.emit(logging.makeLogRecord({'msg':'private-payload'}))
    assert flush(5)
    text = capsys.readouterr().err
    assert text.count('diagnostic evidence degraded') == 1
    assert 'fake-secret' not in text and 'private-payload' not in text
    assert state.snapshot()['degraded'] is True
    assert state.snapshot()['sources'][0]['source'] == 'postgres-logs'


def test_stage_and_mirror_failures_remain_nonfatal(tmp_path, monkeypatch, capsys):
    from delivery_api import log_durability as state
    monkeypatch.setattr(state, '_failures', {})
    from delivery_api import postgres_logs
    def broken(*args,**kwargs):raise OSError('private-payload')
    monkeypatch.setattr(postgres_logs.PostgresLogs,'append',broken)
    blocker = tmp_path/'blocked';blocker.write_text('file')
    monkeypatch.setattr(observability, 'STAGE_LOG_PATHS', {'intent':blocker/'intent.log'})
    observability.event('test', 'not secret', console=False, phase='intent')
    assert flush(5)
    mirror = engine_log._MirrorHandler(blocker/'engine.log', logging.INFO)
    for _ in range(3):mirror.emit(logging.makeLogRecord({'msg':'test'}))
    assert flush(5)
    assert not mirror._dead  # PG degradation does not kill future retries.
    sources = {r['source'] for r in state.snapshot()['sources']}
    assert sources == {'postgres-logs'}
    assert capsys.readouterr().err.count('diagnostic evidence degraded') == 1


def test_degraded_state_requires_operator_actor(monkeypatch):
    from delivery_api import app as module
    from fastapi import HTTPException
    from types import SimpleNamespace
    import pytest
    with pytest.raises(HTTPException) as error:module.debug_log_durability(SimpleNamespace(admin=False))
    assert error.value.status_code == 403
    assert 'degraded' in module.debug_log_durability(SimpleNamespace(admin=True))
