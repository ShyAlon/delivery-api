"""Retained artifacts stay readable when the child has no session checkpoint."""
from contextlib import contextmanager
import pytest
from delivery_api.child_interview import ChildInterviewClient
from delivery_api.engine_client import EngineClientError


def test_missing_session_is_a_typed_read_error_not_an_unhandled_key(monkeypatch):
    class Registry:
        def get(self, sid, *, serve):
            assert sid=='missing' and serve is False
            raise KeyError(sid)
    @contextmanager
    def registry(self):
        yield Registry()
    monkeypatch.setattr(ChildInterviewClient,'_registry',registry)
    client=object.__new__(ChildInterviewClient)
    with pytest.raises(EngineClientError) as error:
        client.snapshot('missing')
    assert error.value.http_status==404
    assert error.value.error.detail=='The interview session is unavailable. Saved task artifacts are unchanged.'


def test_snapshot_serialization_key_error_is_not_mislabeled_as_missing(monkeypatch):
    class Session:
        def snapshot(self):raise KeyError('broken snapshot field')
    class Registry:
        def get(self,*args,**kwargs):return Session()
    @contextmanager
    def registry(self):yield Registry()
    monkeypatch.setattr(ChildInterviewClient,'_registry',registry)
    with pytest.raises(KeyError,match='broken snapshot field'):
        object.__new__(ChildInterviewClient).snapshot('existing')
