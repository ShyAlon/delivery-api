import logging
from delivery_api import observability, engine_log


def test_activity_file_failure_warns_once_and_is_operator_visible(tmp_path, monkeypatch, capsys):
    from delivery_api import log_durability as state
    monkeypatch.setattr(state, '_failures', {})
    handler = observability._EvidenceFileHandler(tmp_path/'activity.log', delay=True)
    def broken(record): raise OSError('fake-secret')
    monkeypatch.setattr(handler, 'stream', None)
    monkeypatch.setattr(handler, '_open', lambda: (_ for _ in ()).throw(OSError('fake-secret')))
    for _ in range(3): handler.emit(logging.makeLogRecord({'msg':'private-payload'}))
    text = capsys.readouterr().err
    assert text.count('diagnostic evidence degraded') == 1
    assert 'fake-secret' not in text and 'private-payload' not in text
    assert state.snapshot()['degraded'] is True
    assert state.snapshot()['sources'][0]['source'] == 'activity-file'


def test_stage_and_mirror_failures_remain_nonfatal(tmp_path, monkeypatch, capsys):
    from delivery_api import log_durability as state
    monkeypatch.setattr(state, '_failures', {})
    blocker = tmp_path/'blocked';blocker.write_text('file')
    monkeypatch.setattr(observability, 'STAGE_LOG_PATHS', {'intent':blocker/'intent.log'})
    observability.event('test', 'not secret', console=False, phase='intent')
    mirror = engine_log._MirrorHandler(blocker/'engine.log', logging.INFO)
    for _ in range(3):mirror.emit(logging.makeLogRecord({'msg':'test'}))
    assert mirror._dead
    sources = {r['source'] for r in state.snapshot()['sources']}
    assert sources == {'stage-file:intent', 'engine-mirror'}
    assert capsys.readouterr().err.count('diagnostic evidence degraded') == 2


def test_degraded_state_requires_operator_actor(monkeypatch):
    from delivery_api import app as module
    from fastapi import HTTPException
    from types import SimpleNamespace
    import pytest
    with pytest.raises(HTTPException) as error:module.debug_log_durability(SimpleNamespace(admin=False))
    assert error.value.status_code == 403
    assert 'degraded' in module.debug_log_durability(SimpleNamespace(admin=True))
