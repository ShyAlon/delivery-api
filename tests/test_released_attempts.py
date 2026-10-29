"""A dispatched provider attempt is released (billed 0) only on proof the provider never received the request."""
import json
import time
from pathlib import Path

import pytest

from delivery_api import attempt_ledger as L
from delivery_api.admission_transport import worker_admission_channel
from delivery_api.attempt_ledger import AdmissionDenied
from delivery_api.producer_admission import WorkerAdmissionGateway

PROOF = {'kind': 'connect-failure', 'error_class': 'ConnectError', 'bytes_sent': 0, 'response_seen': False}


@pytest.fixture
def ledger(pg_workspace_factory):
    from uuid import uuid4
    from delivery_api.tenancy import tenant_context
    with pg_workspace_factory() as workspace,tenant_context(workspace.scope):
        store=workspace.store
        task=store.create_task('Attempt release')['task_id']
        store.set_engine_session(task,'release-'+uuid4().hex)
        job=store.enqueue_worker(task,'fixture:release');assert store.claim_worker(job)
        with workspace.admin() as c:
            c.execute('INSERT INTO credit_accounts(owner_id,balance_units,units_per_credit,updated_at) VALUES(%s,100000000000,1000000000,0)',(workspace.scope.owner_id,))
        def seed(state='dispatched',job_id=None,**fields):
            attempt=uuid4().hex
            with workspace.admin() as c:
                c.execute("INSERT INTO provider_attempts(attempt_id,owner_id,job_id,config_version,quote,fingerprint,state,expense_reserved,retail_reserved,created_at,updated_at) VALUES(%s,%s,%s,'fixture','{}','fixture',%s,37468200000,1,0,%s)",(attempt,workspace.scope.owner_id,job_id or job,state,time.time()))
                c.execute("INSERT INTO credit_charges(attempt_id,owner_id,state,created_at) VALUES(%s,%s,%s,0)",(attempt,workspace.scope.owner_id,'released' if state=='released' else 'pending'))
                for name,value in fields.items():
                    assert name in ('raw_usage','usage','expense_actual','retail_actual','settled_at')
                    is_json=name in ('raw_usage','usage')
                    c.execute('UPDATE provider_attempts SET '+name+'=%s'+('::jsonb' if is_json else '')+' WHERE attempt_id=%s',(json.dumps(value) if is_json else value,attempt))
            return attempt
        def snapshot():
            with workspace.admin() as c:
                return tuple(c.execute(query,(workspace.scope.owner_id,)).fetchall() for query in (
                    'SELECT * FROM provider_attempts WHERE owner_id=%s ORDER BY attempt_id',
                    'SELECT * FROM credit_charges WHERE owner_id=%s ORDER BY attempt_id',
                    'SELECT * FROM credit_accounts WHERE owner_id=%s',
                    'SELECT * FROM retail_contributions WHERE owner_id=%s ORDER BY contribution_key'))+(store.audit_entries(None),)
        yield workspace,store,task,job,seed,snapshot


def test_valid_connect_failure_releases_with_audit_and_no_debit(ledger):
    workspace,store,task,job,seed,snapshot=ledger;attempt=seed();before=snapshot()
    result=store.transition_provider_attempt(attempt,'release',PROOF)
    assert result['state']=='released'
    after=snapshot()
    assert after[2:4]==before[2:4]  # actual balance/remainder and contributions unchanged
    with workspace.admin() as c:
        assert c.execute('SELECT state,charged_units,settled_at FROM credit_charges WHERE attempt_id=%s',(attempt,)).fetchone()==('released',None,None)
    assert all(result[field] is None for field in ('raw_usage','usage','expense_actual','retail_actual','settled_at'))
    added=[a for a in after[4] if a['id'] not in {b['id'] for b in before[4]}]
    audit,=added
    assert (audit['actor'],audit['action'],audit['task_id'])==(workspace.scope.owner_id,'provider.attempt.released',task)
    detail=audit['detail']
    assert detail['billed_units']==0 and detail['evidence']==PROOF and detail['attempt_id']==attempt


@pytest.mark.parametrize('bad', [
    dict(PROOF, error_class='ReadTimeout'), dict(PROOF, error_class='WriteError'), dict(PROOF, error_class='RemoteProtocolError'),
    dict(PROOF, error_class='ConnectionResetError'), dict(PROOF, error_class=None),
    dict(PROOF, bytes_sent=1), dict(PROOF, bytes_sent=True), dict(PROOF, bytes_sent=0.0), dict(PROOF, response_seen=True),
    dict(PROOF, response_seen=None), dict(PROOF, kind='http-5xx'),
    {k: v for k, v in PROOF.items() if k != 'bytes_sent'}, dict(PROOF, extra='x'), None, 'connect', [],
])
def test_only_the_exact_proof_releases(bad,ledger):
    workspace,store,task,job,seed,snapshot=ledger;attempt=seed();before=snapshot()
    with pytest.raises(AdmissionDenied):
        store.transition_provider_attempt(attempt,'release',bad)
    assert snapshot()==before


@pytest.mark.parametrize('state', ['reserved', 'settled', 'uncertain', 'cancelled', 'failure_waived'])
def test_only_a_dispatched_attempt_can_be_released(state,ledger):
    workspace,store,task,job,seed,snapshot=ledger;attempt=seed(state);before=snapshot()
    with pytest.raises(AdmissionDenied):
        store.transition_provider_attempt(attempt,'release',PROOF)
    assert snapshot()==before


@pytest.mark.parametrize('field', ['raw_usage', 'usage', 'expense_actual', 'retail_actual', 'settled_at'])
def test_an_attempt_with_usage_evidence_is_never_released(field,ledger):
    workspace,store,task,job,seed,snapshot=ledger;attempt=seed(**{field:{'x':1} if field in ('raw_usage','usage') else 5});before=snapshot()
    with pytest.raises(AdmissionDenied):
        store.transition_provider_attempt(attempt,'release',PROOF)
    assert snapshot()==before


def test_replay_of_a_released_attempt_changes_nothing(ledger):
    workspace,store,task,job,seed,snapshot=ledger;attempt=seed('released');before=snapshot()
    assert store.transition_provider_attempt(attempt,'release',PROOF)['state']=='released'
    assert snapshot()==before


def test_release_limits_per_job_and_per_owner_per_day(ledger):
    workspace,store,task,job,seed,snapshot=ledger
    for _ in range(L.RELEASE_MAX_PER_JOB-1):seed('released')
    attempt=seed()
    assert store.transition_provider_attempt(attempt,'release',PROOF)['state']=='released'
    blocked=seed();before=snapshot()
    with pytest.raises(AdmissionDenied,match='release limit'):store.transition_provider_attempt(blocked,'release',PROOF)
    assert snapshot()==before
    other_job=store.enqueue_worker(task,'fixture:other');assert store.claim_worker(other_job)
    for _ in range(L.RELEASE_MAX_PER_OWNER_DAY-L.RELEASE_MAX_PER_JOB-1):seed('released',job_id=other_job)
    # Owner is one below cap; a fresh job is below its own cap.
    fresh=store.enqueue_worker(task,'fixture:fresh');assert store.claim_worker(fresh)
    candidate=seed(job_id=fresh)
    assert store.transition_provider_attempt(candidate,'release',PROOF)['state']=='released'
    blocked=seed(job_id=fresh);before=snapshot()
    with pytest.raises(AdmissionDenied,match='release limit'):store.transition_provider_attempt(blocked,'release',PROOF)
    assert snapshot()==before


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


# ---- migration ----
MIGRATION = next((Path(__file__).resolve().parent.parent / 'supabase' / 'migrations').glob('*_released_provider_attempts.sql'))


def test_migration_adds_the_state_idempotently_and_keeps_released_unbilled():
    sql = MIGRATION.read_text()
    for needle in ["'released'", 'provider_attempts_state_check', 'credit_charges_state_check',
                   'provider_attempts_released_unbilled', 'credit_charges_released_no_debit']:
        assert needle in sql
    for old in ("'reserved','dispatched','settled','cancelled','uncertain','failure_waived'", "'pending','settled','failure_waived'"):
        assert old in sql  # every state that existed before stays allowed
    assert sql.count('DROP CONSTRAINT IF EXISTS') == sql.count('ADD CONSTRAINT') == 4
