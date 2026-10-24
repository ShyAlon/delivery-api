"""publish_committed_session: flag-gated publication of committed phase bytes;
failure is recorded on the task, never raised into the settled run."""
from delivery_api import artifact_publication, config


class FakeStore:
    def __init__(self, fail=False):
        self.events = []
        self.fail = fail
        self.published = []
    def add_event(self, task_id, kind, message, phase_key=None, payload=None):
        self.events.append((kind, message, phase_key))
    def publish_task_revision(self, task_id, artifacts, *, writer_instance):
        if self.fail:
            raise RuntimeError('database down')
        self.published.append((task_id, artifacts))
        return [{'name': a['name']} for a in artifacts]


def _committed(tmp_path):
    (tmp_path / 'spec').mkdir()
    (tmp_path / 'spec' / 'needs.json').write_text('{"contract_version":"1","needs":[]}')


def test_flag_off_is_a_noop(monkeypatch, tmp_path):
    _committed(tmp_path)
    monkeypatch.setattr(config, 'ARTIFACT_PUBLISH', False, raising=False)
    store = FakeStore()
    assert artifact_publication.publish_committed_session(store, 't1', tmp_path, phase_key='needs') is False
    assert store.published == [] and store.events == []


def test_flag_on_publishes_the_present_artifacts(monkeypatch, tmp_path):
    _committed(tmp_path)
    monkeypatch.setattr(config, 'ARTIFACT_PUBLISH', True, raising=False)
    store = FakeStore()
    assert artifact_publication.publish_committed_session(store, 't1', tmp_path, phase_key='needs') is True
    assert store.published and store.published[0][0] == 't1'
    assert [a['name'] for a in store.published[0][1]] == ['needs.json']
    assert store.events == []


def test_publication_failure_is_recorded_and_never_raised(monkeypatch, tmp_path):
    _committed(tmp_path)
    monkeypatch.setattr(config, 'ARTIFACT_PUBLISH', True, raising=False)
    store = FakeStore(fail=True)
    assert artifact_publication.publish_committed_session(store, 't1', tmp_path, phase_key='needs') is False
    assert len(store.events) == 1
    kind, message, phase_key = store.events[0]
    assert kind == 'error' and phase_key == 'needs' and 'republished without rerunning' in message
