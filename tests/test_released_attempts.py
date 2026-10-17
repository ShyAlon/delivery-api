"""A dispatched provider attempt is released (billed 0) only on proof the provider never received the request."""
import json
import socket
from pathlib import Path

import pytest

from delivery_api import attempt_ledger as L
from delivery_api.admission_transport import worker_admission_channel
from delivery_api.attempt_ledger import AdmissionDenied, release_proof, transition
from delivery_api.producer_admission import WorkerAdmissionGateway

PROOF = {'kind': 'connect-failure', 'error_class': 'ConnectError', 'bytes_sent': 0, 'response_seen': False}


def _row(**kw):
    base = {'attempt_id': 'a1', 'owner_id': 'o1', 'job_id': 'j1', 'state': 'dispatched', 'expense_reserved': 37468200000,
            'raw_usage': None, 'usage': None, 'expense_actual': None, 'retail_actual': None, 'settled_at': None,
            'quote': {}}
    base.update(kw)
    return base


class Cur:
    def __init__(self, rows):
        self.rows = rows

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return list(self.rows)


class Db:
    def __init__(self, row, per_job=0, per_owner=0):
        self.row, self.per_job, self.per_owner, self.writes = row, per_job, per_owner, []

    def execute(self, sql, params=()):
        s = ' '.join(sql.split())
        if 'FROM provider_attempts WHERE attempt_id=? FOR UPDATE' in s:
            return Cur([self.row])
        if 'count(*)' in s and 'job_id=?' in s:
            return Cur([{'n': self.per_job}])
        if 'count(*)' in s and 'owner_id=?' in s:
            return Cur([{'n': self.per_owner}])
        if s.startswith(('UPDATE', 'INSERT')):
            self.writes.append((s, params))
        return Cur([])


class Store:
    def __init__(self, row, **kw):
        self._db = Db(row, **kw)
        self.audits = []

    def audit(self, actor, action, task_id=None, detail=None):
        self.audits.append((actor, action, task_id, detail))

    def worker_job(self, job_id):
        return {'task_id': 't1'}

    def provider_attempt(self, attempt_id):
        return dict(self._db.row)


@pytest.fixture(autouse=True)
def no_locks(monkeypatch):
    import delivery_api.prepaid as prepaid
    monkeypatch.setattr(prepaid, 'owner_lock', lambda store: None)


def test_valid_connect_failure_releases_with_audit_and_no_debit():
    store = Store(_row())
    transition(store, 'a1', 'release', PROOF)
    sqls = [w[0] for w in store._db.writes]
    assert any("SET state='released'" in s and "state='dispatched'" in s for s in sqls)
    assert any("credit_charges SET state='released'" in s and "state='pending'" in s for s in sqls)
    assert not any('balance_units' in s or "state='settled'" in s for s in sqls)
    (actor, action, task, detail), = store.audits
    assert (actor, action, task) == ('o1', 'provider.attempt.released', 't1')
    assert detail['billed_units'] == 0 and detail['evidence'] == PROOF and detail['attempt_id'] == 'a1'


@pytest.mark.parametrize('bad', [
    dict(PROOF, error_class='ReadTimeout'), dict(PROOF, error_class='WriteError'), dict(PROOF, error_class='RemoteProtocolError'),
    dict(PROOF, error_class='ConnectionResetError'), dict(PROOF, error_class=None),
    dict(PROOF, bytes_sent=1), dict(PROOF, bytes_sent=True), dict(PROOF, bytes_sent=0.0), dict(PROOF, response_seen=True),
    dict(PROOF, response_seen=None), dict(PROOF, kind='http-5xx'),
    {k: v for k, v in PROOF.items() if k != 'bytes_sent'}, dict(PROOF, extra='x'), None, 'connect', [],
])
def test_only_the_exact_proof_releases(bad):
    store = Store(_row())
    with pytest.raises(AdmissionDenied):
        transition(store, 'a1', 'release', bad)
    assert not store._db.writes and not store.audits


@pytest.mark.parametrize('state', ['reserved', 'settled', 'uncertain', 'cancelled', 'failure_waived'])
def test_only_a_dispatched_attempt_can_be_released(state):
    store = Store(_row(state=state))
    with pytest.raises(AdmissionDenied):
        transition(store, 'a1', 'release', PROOF)
    assert not store._db.writes


@pytest.mark.parametrize('field', ['raw_usage', 'usage', 'expense_actual', 'retail_actual', 'settled_at'])
def test_an_attempt_with_usage_evidence_is_never_released(field):
    store = Store(_row(**{field: {'x': 1} if field in ('raw_usage', 'usage') else 5}))
    with pytest.raises(AdmissionDenied):
        transition(store, 'a1', 'release', PROOF)
    assert not store._db.writes


def test_replay_of_a_released_attempt_changes_nothing():
    store = Store(_row(state='released'))
    assert transition(store, 'a1', 'release', PROOF)['state'] == 'released'
    assert not store._db.writes and not store.audits


def test_release_limits_per_job_and_per_owner_per_day():
    for kw in (dict(per_job=L.RELEASE_MAX_PER_JOB), dict(per_owner=L.RELEASE_MAX_PER_OWNER_DAY)):
        store = Store(_row(), **kw)
        with pytest.raises(AdmissionDenied, match='release limit'):
            transition(store, 'a1', 'release', PROOF)
        assert not store._db.writes
    store = Store(_row(), per_job=L.RELEASE_MAX_PER_JOB - 1, per_owner=L.RELEASE_MAX_PER_OWNER_DAY - 1)
    transition(store, 'a1', 'release', PROOF)
    assert store._db.writes


def test_released_never_counts_as_held_or_spent_by_admission():
    src = Path(L.__file__).read_text()
    # admission holds are exactly reserved/dispatched/uncertain; spend is settled/reserved-held/failure_waived
    assert "r['state'] in ('reserved','dispatched','uncertain')" in src
    assert "'released'" not in src.split('def reserve(')[1].split('def transition(')[0]


# ---- the worker gateway: meter resumes, ledger decides ----
class GwStore:
    def __init__(self, fail=False):
        self.calls, self.fail = [], fail

    def provider_attempt(self, attempt):
        return {'job_id': 'j1', 'state': 'dispatched'}

    def transition_provider_attempt(self, attempt, action, usage=None, **kw):
        self.calls.append((attempt, action, usage))
        if self.fail:
            raise AdmissionDenied('nope')


class Meter:
    def __init__(self):
        self.resumed = 0

    def resume(self):
        self.resumed += 1


def test_gateway_release_passes_the_proof_and_resumes_the_meter():
    g = WorkerAdmissionGateway(GwStore(), 'j1')
    g.compute_meter = Meter(); g._meter_attempts.add('a1')
    g.release('a1', PROOF)
    assert g.store.calls == [('a1', 'release', PROOF)] and g.compute_meter.resumed == 1


def test_gateway_release_refusal_propagates_and_leaves_the_meter_alone():
    g = WorkerAdmissionGateway(GwStore(fail=True), 'j1')
    g.compute_meter = Meter(); g._meter_attempts.add('a1')
    with pytest.raises(AdmissionDenied):
        g.release('a1', PROOF)
    assert g.compute_meter.resumed == 0


# ---- the socket protocol ----
def _ask(gateway, action, payload):
    with worker_admission_channel(gateway) as child:
        f = child.makefile('rwb')
        f.write(json.dumps({'action': action, 'payload': payload}).encode() + b'\n'); f.flush()
        reply = json.loads(f.readline())
        f.close()
    return reply


class Gw:
    def __init__(self):
        self.got = None

    def release(self, attempt, evidence):
        self.got = (attempt, evidence)
        return {'state': 'released'}


def test_socket_release_action_reaches_the_gateway():
    gw = Gw()
    assert _ask(gw, 'release', {'attempt': 'a1', 'evidence': PROOF})['ok'] is True
    assert gw.got == ('a1', PROOF)


@pytest.mark.parametrize('payload', [{'attempt': 'a1'}, {'attempt': 'a1', 'evidence': PROOF, 'x': 1}, {}])
def test_socket_release_rejects_malformed_payloads(payload):
    gw = Gw()
    assert _ask(gw, 'release', payload)['ok'] is False and gw.got is None


# ---- migration and resume ----
MIGRATION = next((Path(__file__).resolve().parent.parent / 'supabase' / 'migrations').glob('*_released_provider_attempts.sql'))


def test_migration_adds_the_state_idempotently_and_keeps_released_unbilled():
    sql = MIGRATION.read_text()
    for needle in ["'released'", 'provider_attempts_state_check', 'credit_charges_state_check',
                   'provider_attempts_released_unbilled', 'credit_charges_released_no_debit']:
        assert needle in sql
    for old in ("'reserved','dispatched','settled','cancelled','uncertain','failure_waived'", "'pending','settled','failure_waived'"):
        assert old in sql  # every state that existed before stays allowed
    assert sql.count('DROP CONSTRAINT IF EXISTS') == sql.count('ADD CONSTRAINT') == 4


def test_resume_reapplies_the_migration_after_the_dump_restore(tmp_path, monkeypatch):
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'scripts' / 'item6'))
    import resume_entry
    (tmp_path / '20261017100000_released_provider_attempts.sql').write_text(MIGRATION.read_text())
    ran = []

    class C:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def execute(self, sql, prepare=None): ran.append(sql)
    import types
    monkeypatch.setitem(sys.modules, 'psycopg', types.SimpleNamespace(connect=lambda dsn: C()))
    assert resume_entry.apply_ledger_schema('host=x', str(tmp_path)).endswith('released_provider_attempts.sql')
    assert ran and "'released'" in ran[0]
    with pytest.raises(ValueError, match='missing'):
        resume_entry.apply_ledger_schema('host=x', str(tmp_path / 'none'))


def test_resume_discovery_does_not_count_a_released_attempt_as_unsettled():
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'scripts' / 'item6'))
    import recovery_resume as R

    class C:
        def execute(self, sql, params=()):
            s = ' '.join(sql.split())
            if 'FROM worker_jobs' in s:
                return Cur([{'job_id': 'i', 'kind': 'phase:intent', 'state': 'done', 'task_id': 't', 'attempt': 1, 'generation': 1, 'session_id': 's', 'auth_session_id': 'x'},
                            {'job_id': 'n', 'kind': 'phase:needs', 'state': 'failed', 'task_id': 't', 'attempt': 1, 'generation': 1, 'session_id': 's', 'auth_session_id': 'x'}])
            if 'FROM provider_attempts' in s:
                base = {'job_id': 'n', 'provider_started_at': None, 'expense_reserved': 1, 'retail_reserved': 1}
                return Cur([dict(base, attempt_id='s1', state='settled'), dict(base, attempt_id='r1', state='released'),
                            dict(base, attempt_id='h1', state='reserved')])
            raise RuntimeError('past the held-attempt check')
    with pytest.raises(RuntimeError, match='past the held-attempt check'):
        R.discover(C())
