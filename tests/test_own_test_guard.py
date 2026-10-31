"""Own-test profile is operator-only; real PG dispatch path, no provider network."""
import uuid
import pytest
from delivery_api.tenancy import tenant_context
from delivery_api.attempt_ledger import AdmissionDenied
from test_attempt_ledger import credit_account, A, B, DSN, LAUNCH_Q, LAUNCH_USAGE

@pytest.fixture
def profile(credit_account):
    import psycopg
    s,tid,job=credit_account
    with psycopg.connect(DSN) as c:
        c.execute('INSERT INTO own_test_expense_profiles VALUES(%s,%s,2,1)',(A.owner_id,'test-reviewed'))
    yield s,tid,job
    with psycopg.connect(DSN) as c:c.execute('DELETE FROM own_test_expense_profiles WHERE owner_id=%s',(A.owner_id,))

def test_real_dispatch_ignores_large_quote_then_blocks_only_new_calls(profile):
    s,tid,job=profile
    with tenant_context(A):
        a,b=uuid.uuid4().hex,uuid.uuid4().hex
        for aid in (a,b):
            s.reserve_provider_attempt(job,aid,LAUNCH_Q,aid)
            s.transition_provider_attempt(aid,'start',require_prepaid=True)
        s.transition_provider_attempt(a,'settle',LAUNCH_USAGE,require_prepaid=True)
        # Already-dispatched call finishes and debits after threshold.
        s.transition_provider_attempt(b,'settle',LAUNCH_USAGE,require_prepaid=True)
        nxt=uuid.uuid4().hex;s.reserve_provider_attempt(job,nxt,LAUNCH_Q,nxt)
        with pytest.raises(AdmissionDenied,match='own_test_rolling_24h_expense_limit'):
            s.transition_provider_attempt(nxt,'start',require_prepaid=True)
        assert s.provider_attempt(nxt)['state']=='reserved'
        s.transition_provider_attempt(nxt,'cancel')

def test_scale_unknown_and_window(profile):
    import psycopg,time
    s,tid,job=profile
    with tenant_context(A):
        bad=uuid.uuid4().hex;s.reserve_provider_attempt(job,bad,LAUNCH_Q|{'expense_units_per_usd':10},bad)
        with pytest.raises(AdmissionDenied,match='own_test_expense_scale_mismatch'):s.transition_provider_attempt(bad,'start')
        s.transition_provider_attempt(bad,'cancel')
        a=uuid.uuid4().hex;s.reserve_provider_attempt(job,a,LAUNCH_Q,a);s.transition_provider_attempt(a,'start')
        s.transition_provider_attempt(a,'settle',LAUNCH_USAGE)
        with psycopg.connect(DSN) as c:c.execute('UPDATE provider_attempts SET settled_at=%s WHERE attempt_id=%s',(time.time()-86401,a))
        b=uuid.uuid4().hex;s.reserve_provider_attempt(job,b,LAUNCH_Q,b);s.transition_provider_attempt(b,'start')
        s.transition_provider_attempt(b,'uncertain')
        # Direct DB guard check, because the ordinary reserve path already rejects uncertain usage.
        with s._postgres.transaction() as c:
            assert c.execute('SELECT requi_own_test_expense_guard(%s,1) AS reason',(job,)).fetchone()['reason']=='own_test_unknown_provider_outcome'

def test_profile_hidden_from_backend(profile):
    import psycopg
    s,_,job=profile
    with tenant_context(A),s._postgres.transaction() as c:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):c.execute('SELECT * FROM own_test_expense_profiles')
    with tenant_context(B),s._postgres.transaction() as c:
        assert c.execute('SELECT requi_own_test_expense_guard(%s,1) AS reason',(job,)).fetchone()['reason']=='worker_unavailable'


def test_explicit_transition_atomic_and_idempotent(credit_account):
    import psycopg
    from delivery_api.own_test_transition import transition,SCALE
    s,tid,job=credit_account
    with tenant_context(A):s.finish_worker(job)
    with psycopg.connect(DSN) as c:
        email=c.execute('SELECT email FROM auth.users WHERE id=%s',(A.owner_id,)).fetchone()[0]
        c.execute('UPDATE admission_limits SET expense_limit=%s,period_seconds=86400 WHERE owner_id=%s',(2*SCALE,A.owner_id))
        before=c.execute('SELECT * FROM admission_limits WHERE owner_id=%s',(A.owner_id,)).fetchone()
        plan=transition(c,email)
        assert plan['old_expense_limit']==2*SCALE
        assert c.execute('SELECT * FROM admission_limits WHERE owner_id=%s',(A.owner_id,)).fetchone()==before
        assert c.execute('SELECT * FROM own_test_expense_profiles WHERE owner_id=%s',(A.owner_id,)).fetchone() is None
        transition(c,email,apply=True)
        assert c.execute('SELECT expense_limit FROM admission_limits WHERE owner_id=%s',(A.owner_id,)).fetchone()==(None,)
        assert transition(c,email,apply=True)['already_applied']
        c.rollback() # demonstrate both profile/cap changes roll back together
    with psycopg.connect(DSN) as c:
        assert c.execute('SELECT * FROM own_test_expense_profiles WHERE owner_id=%s',(A.owner_id,)).fetchone() is None


def test_transition_refuses_active_work_and_other_caps(credit_account):
    import psycopg
    from delivery_api.own_test_transition import transition,SCALE
    s,tid,job=credit_account
    with psycopg.connect(DSN) as c:
        email=c.execute('SELECT email FROM auth.users WHERE id=%s',(A.owner_id,)).fetchone()[0]
        c.execute('UPDATE admission_limits SET expense_limit=%s,period_seconds=86400 WHERE owner_id=%s',(2*SCALE,A.owner_id))
        with pytest.raises(AdmissionDenied,match='owner work') as refusal:transition(c,email,apply=True)
        assert tid in str(refusal.value) and job in str(refusal.value)
        assert 'state=running' in str(refusal.value) or 'state=pending' in str(refusal.value)
        c.execute('UPDATE admission_limits SET rate_max=1 WHERE owner_id=%s',(A.owner_id,))
        with pytest.raises(AdmissionDenied,match='no owner quota'):transition(c,email,apply=True)
        c.rollback()


def test_production_factory_path_checks_confirmed_profile(profile):
    import base64,json,psycopg
    from delivery_api import production_gateway as pg
    from test_production_gateway import env as gateway_env
    s,tid,job=profile
    # Give enough prepaid balance; provider balance transport and actual provider are never called.
    factory=pg.ProductionGateways(s,gateway_env(),vendor_fetcher=lambda u:(_ for _ in ()).throw(RuntimeError('offline')))
    try:
        with tenant_context(A):
            gw=factory(None,job)
            req=base64.b64encode(json.dumps({'model':'deepseek-test-model','max_tokens':8000}).encode()).decode()
            aid=uuid.uuid4().hex
            gw.reserve_serialized(attempt_id=aid,stage='preflight',provider='deepseek',model='deepseek-test-model',serialized_request=req,max_output_tokens=8000)
            # Factory quote uses 1e12 units/USD, fixture profile intentionally uses scale1.
            # Replace only remote checks, retaining real gateway start and PG transition.
            gw._verify_snapshot=lambda:None
            gw.provider_balance=lambda:{'response':{'is_available':True,'balance_infos':[{'currency':'USD','total_balance':'100','granted_balance':'0','topped_up_balance':'100'}]},'observed_at':__import__('datetime').datetime.now(__import__('datetime').timezone.utc).isoformat()}
            with pytest.raises(AdmissionDenied,match='own_test_expense_scale_mismatch'):gw.start(aid)
            assert s.provider_attempt(aid)['state']=='cancelled'
    finally:factory.close()


def test_settlement_serializes_with_next_dispatch(profile):
    """A start waiting behind committed settlement observes the crossed tripwire."""
    import threading
    from concurrent.futures import ThreadPoolExecutor
    from delivery_api.postgres_store import PostgresStore
    s,tid,job=profile
    with tenant_context(A):
        a,b=uuid.uuid4().hex,uuid.uuid4().hex
        s.reserve_provider_attempt(job,a,LAUNCH_Q,a);s.transition_provider_attempt(a,'start')
        s.reserve_provider_attempt(job,b,LAUNCH_Q,b)
        with s._postgres.transaction():
            s.transition_provider_attempt(a,'settle',LAUNCH_USAGE)
            entered=threading.Event()
            def start():
                db=PostgresStore(DSN.replace('user=sandbox','user=delivery_app'))
                with tenant_context(A):
                    entered.set()
                    try:db.transition_provider_attempt(b,'start');return 'started'
                    except AdmissionDenied as exc:return str(exc)
            pool=ThreadPoolExecutor(max_workers=1);future=pool.submit(start)
            assert entered.wait(5)
            assert not future.done()
        assert future.result(timeout=5)=='own_test_rolling_24h_expense_limit'
        pool.shutdown()
        s.transition_provider_attempt(b,'cancel')


def test_profile_does_not_apply_to_explicit_local_route(profile):
    s,_,job=profile
    with tenant_context(A):
        quote=LAUNCH_Q|{'provider':'ollama','expense_input':0,'expense_output':0,'expense_cache_hit':0,'expense_cache_miss':0}
        aid=uuid.uuid4().hex;s.reserve_provider_attempt(job,aid,quote,aid)
        assert s.transition_provider_attempt(aid,'start')['state']=='dispatched'
        assert s.transition_provider_attempt(aid,'settle',LAUNCH_USAGE)['expense_actual']==0


def test_future_or_mixed_scale_settled_expense_is_not_free(profile):
    import psycopg,time
    s,_,job=profile
    with tenant_context(A):
        aid=uuid.uuid4().hex;s.reserve_provider_attempt(job,aid,LAUNCH_Q,aid);s.transition_provider_attempt(aid,'start')
        s.transition_provider_attempt(aid,'settle',LAUNCH_USAGE)
        for future in (True,False):
            with psycopg.connect(DSN) as c:
                if future:c.execute('UPDATE provider_attempts SET settled_at=%s WHERE attempt_id=%s',(time.time()+60,aid))
                else:c.execute("UPDATE provider_attempts SET settled_at=%s,quote=jsonb_set(quote,'{expense_units_per_usd}','10') WHERE attempt_id=%s",(time.time(),aid))
            with s._postgres.transaction() as c:
                assert c.execute('SELECT requi_own_test_expense_guard(%s,1) AS reason',(job,)).fetchone()['reason']=='own_test_expense_history_unverifiable'


def test_transition_does_not_erase_unresolved_compute(credit_account):
    import psycopg
    from delivery_api.own_test_transition import transition,SCALE
    s,_,job=credit_account
    with tenant_context(A):s.finish_worker(job)
    with psycopg.connect(DSN) as c:
        email=c.execute('SELECT email FROM auth.users WHERE id=%s',(A.owner_id,)).fetchone()[0]
        c.execute('UPDATE admission_limits SET expense_limit=%s,period_seconds=86400 WHERE owner_id=%s',(2*SCALE,A.owner_id))
        c.execute("INSERT INTO retail_compute_segments(segment_key,owner_id,job_id,phase_key,rates,state,started_at) VALUES(%s,%s,%s,'needs','{}','unresolved',0)",('profile-compute-'+uuid.uuid4().hex,A.owner_id,job))
        with pytest.raises(AdmissionDenied,match='compute accounting'):transition(c,email,apply=True)
        assert c.execute('SELECT * FROM own_test_expense_profiles WHERE owner_id=%s',(A.owner_id,)).fetchone() is None
        c.rollback()
