"""2026-11-10: GET /execution ran 4x (auth_session_active + task + session_binding) per document read: 3 statements x 4
reads, about 620 ms per group of 4. The merged snapshot makes each validation one statement; it stays fresh per call."""
import pytest

from delivery_api.historical_checkpoint import HistoricalCheckpointCapability
from delivery_api.tenancy import TenantAccessDenied, current_scope


class Scope:
    owner_id = 'o1'


class Store:
    def __init__(self, snapshot=True, active=True, task_owner='o1', binding_owner='o1'):
        self.calls = []
        self.active = active
        self.task_owner = task_owner
        self.binding_owner = binding_owner
        if snapshot:
            self.authority_snapshot = self._snapshot

    def _task(self):
        return {'owner_id': self.task_owner, 'generation': 1}

    def _binding(self):
        return {'owner_id': self.binding_owner, 'task_id': 't', 'generation': 1}

    def _snapshot(self, task_id, session_id):
        self.calls.append('authority_snapshot')
        return self._task(), self._binding(), self.active

    def auth_session_active(self):
        self.calls.append('auth_session_active')
        return self.active

    def task(self, task_id):
        self.calls.append('task')
        return self._task()

    def session_binding(self, session_id):
        self.calls.append('session_binding')
        return self._binding()


@pytest.fixture
def cap(monkeypatch):
    scope = Scope()
    monkeypatch.setattr('delivery_api.historical_checkpoint.current_scope', lambda: scope)
    return HistoricalCheckpointCapability(scope, 't', 's', 1)


def test_validate_is_one_statement_with_the_merged_snapshot(cap):
    store = Store()
    assert cap.validate(store)['owner_id'] == 'o1'
    assert store.calls == ['authority_snapshot']


def test_validate_is_fresh_on_every_call(cap):
    store = Store()
    cap.validate(store);cap.validate(store)
    assert store.calls == ['authority_snapshot', 'authority_snapshot']


def test_stores_without_the_snapshot_still_read_all_three(cap):
    store = Store(snapshot=False)
    cap.validate(store)
    assert sorted(store.calls) == ['auth_session_active', 'session_binding', 'task']


@pytest.mark.parametrize('snapshot', [True, False])
@pytest.mark.parametrize('kw,message', [({'active': False}, 'owner revoked'), ({'task_owner': 'other'}, 'binding differs'), ({'binding_owner': 'other'}, 'binding differs')])
def test_revoked_or_foreign_authority_is_denied(cap, snapshot, kw, message):
    with pytest.raises(TenantAccessDenied, match=message):
        cap.validate(Store(snapshot=snapshot, **kw))
