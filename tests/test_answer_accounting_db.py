"""Real isolated PG, backend-role RLS, no runtime/provider activation."""
from decimal import Decimal
import json
from uuid import uuid4
from concurrent.futures import ThreadPoolExecutor
import psycopg
import pytest
from delivery_api.answer_pricing import AnswerPricing
from delivery_api.tenancy import tenant_context

@pytest.fixture
def answer_workspace(pg_workspace_factory):
    with pg_workspace_factory() as w:
        with w.admin() as c:
            c.execute('INSERT INTO credit_accounts(owner_id,balance_units,units_per_credit,updated_at) VALUES(%s,1000000000,1000000000,0)',(w.scope.owner_id,))
        with tenant_context(w.scope):
            tid=w.store.create_task('Answer pricing foundation')['task_id']
            w.store.set_engine_session(tid,'answer-'+uuid4().hex)
            job=w.store.enqueue_worker(tid,'phase:intent');assert w.store.claim_worker(job)
        try:yield w,job
        finally:
            with w.admin() as c:
                c.execute('DELETE FROM answer_charge_usage WHERE owner_id=%s',(w.scope.owner_id,))
                c.execute('DELETE FROM answer_charge_receipts WHERE owner_id=%s',(w.scope.owner_id,))


def begin(w,job,aid='answer',input_id='input',pricing=None,digest='a'*64):
    with tenant_context(w.scope),w.store._postgres.transaction() as c:
        return c.execute('SELECT requi_answer_start(%s,%s,%s,%s,%s::jsonb) AS r',(aid,job,input_id,digest,json.dumps(pricing or AnswerPricing.from_environment({}).snapshot()))).fetchone()['r']


def usage(tokens=1000,ms='0.003'):
    return {'attempts':[{'attempt_id':'attempt-1','phase':'intent','input_tokens':tokens,'output_tokens':0}], 'deterministic_ms':ms}


def finish(w,aid='answer',u=None):
    with tenant_context(w.scope),w.store._postgres.transaction() as c:
        return c.execute('SELECT requi_answer_finish(%s,%s::jsonb) AS r',(aid,json.dumps(usage() if u is None else u))).fetchone()['r']


def balance(w):
    with w.admin() as c:
        return c.execute('SELECT balance_units,fractional_debit_units FROM credit_accounts WHERE owner_id=%s',(w.scope.owner_id,)).fetchone()


def test_atomic_estimate_and_refund_replay_and_audit(answer_workspace):
    w,j=answer_workspace
    r=begin(w,j);assert r['estimate_units']==100000000 and balance(w)==(900000000,Decimal(0))
    assert begin(w,j)==r
    # Config edits do not reprice/redebit an existing answer.
    assert begin(w,j,pricing=AnswerPricing.from_environment({'DELIVERY_ANSWER_START_CREDITS':'0.2'}).snapshot())==r
    result=finish(w);assert result['actual_units']==12500000 and balance(w)==(987500000,Decimal('0.150'))
    assert finish(w)==result
    with w.admin() as c:
        audit=c.execute("SELECT kind,delta_units,reference FROM credit_ledger WHERE owner_id=%s AND reference='answer' ORDER BY entry_id",(w.scope.owner_id,)).fetchall()
    assert audit==[('debit',-100000000,'answer'),('adjustment',87500000,'answer')]


def test_overrun_nonblocking_negative_balance_and_statistics(answer_workspace):
    w,j=answer_workspace;begin(w,j)
    result=finish(w,u=usage(100000))
    assert result['actual_units']==1250000000 and result['overrun'] is True
    assert balance(w)==(-250000000,Decimal('0.150'))
    assert finish(w,u=usage(100000))==result
    with w.admin() as c:
        assert c.execute("SELECT count(*) FROM answer_charge_receipts WHERE owner_id=%s AND state='settled' AND overrun",(w.scope.owner_id,)).fetchone()==(1,)


def test_concurrent_start_and_settle_are_once_only(answer_workspace):
    w,j=answer_workspace
    with w.admin() as c:c.execute('UPDATE credit_accounts SET balance_units=100000000 WHERE owner_id=%s',(w.scope.owner_id,))
    def start_one(i):
        try:begin(w,j,aid='a'+str(i),input_id='i'+str(i));return 'accepted'
        except psycopg.Error as e:
            assert 'insufficient' in str(e);return 'insufficient'
    with ThreadPoolExecutor(4) as pool:out=list(pool.map(start_one,range(4)))
    assert out.count('accepted')==1 and balance(w)[0]==0
    with w.admin() as c:aid=c.execute('SELECT answer_id FROM answer_charge_receipts WHERE owner_id=%s',(w.scope.owner_id,)).fetchone()[0]
    with ThreadPoolExecutor(4) as pool:results=list(pool.map(lambda _:finish(w,aid),range(4)))
    assert all(x==results[0] for x in results)
    assert balance(w)==(87500000,Decimal('0.150'))


def test_cross_owner_and_browser_roles_cannot_use_receipts(answer_workspace,pg_workspace_factory):
    w,j=answer_workspace;begin(w,j)
    with pg_workspace_factory() as other:
        with pytest.raises(psycopg.Error):finish(other)
        with pytest.raises(psycopg.Error):begin(other,j,aid='stolen')
    with w.admin() as c:
        for role in ['anon','authenticated','service_role']:
            for function in ['requi_answer_start(text,text,text,text,jsonb)','requi_answer_finish(text,jsonb)']:
                assert not c.execute('SELECT has_function_privilege(%s,%s,\'EXECUTE\')',(role,function)).fetchone()[0]
            assert not c.execute('SELECT has_table_privilege(%s,\'answer_charge_receipts\',\'SELECT\')',(role,)).fetchone()[0]
    assert balance(w)[0]==900000000


def test_conflicts_and_invalid_usage_leave_estimate_no_partial_correction(answer_workspace):
    w,j=answer_workspace;begin(w,j)
    with pytest.raises(psycopg.Error,match='conflicting'):begin(w,j,digest='b'*64)
    for u in [usage(-1),usage(True),usage(ms='NaN'),usage()|{'extra':1}]:
        with pytest.raises(psycopg.Error):finish(w,u=u)
    assert balance(w)==(900000000,Decimal(0))
    finish(w)
    with pytest.raises(psycopg.Error,match='conflicting'):finish(w,u=usage(1001))


def test_sql_price_matches_existing_formula_with_frozen_rates(answer_workspace):
    w,j=answer_workspace
    rates={'DELIVERY_ANSWER_START_CREDITS':'0.125','PRICE_PER_TOKEN':'0.000002','PRICE_PER_10MS':'0.000000003'}
    p=AnswerPricing.from_environment(rates)
    with w.admin() as c:c.execute('UPDATE credit_accounts SET fractional_debit_units=0.7 WHERE owner_id=%s',(w.scope.owner_id,))
    begin(w,j,pricing=p.snapshot())
    expected=p.settle(input_tokens=7,output_tokens=0,deterministic_ms='0.25',remainder=Decimal('0.7'))
    r=finish(w,u=usage(7,'0.25'))
    assert r['actual_units']==expected.actual_units
    assert balance(w)==(1000000000-expected.actual_units,expected.remainder)


def test_missing_session_and_invalid_frozen_config_never_debit(answer_workspace):
    from copy import deepcopy
    w,j=answer_workspace;p=AnswerPricing.from_environment({}).snapshot()
    bad=[]
    for path,val in [(('version',),None),(('estimate_units',),0),(('start_credits',),'0.2'),(('rates','formula_version'),None),(('rates','units_per_credit'),None),(('rates','price_per_token'),'NaN'),(('rates','price_per_10ms'),'0.0000000001')]:
        x=deepcopy(p);target=x
        for k in path[:-1]:target=target[k]
        target[path[-1]]=val;bad.append(x)
    for n,x in enumerate(bad):
        with pytest.raises(psycopg.Error):begin(w,j,aid='bad-'+str(n),input_id='i'+str(n),pricing=x)
    assert balance(w)==(1000000000,Decimal(0))
    with w.admin() as c:c.execute('DELETE FROM auth.sessions WHERE id=%s',(w.scope.session_id,))
    try:
        with pytest.raises(psycopg.Error):begin(w,j)
        assert balance(w)[0]==1000000000
    finally:
        with w.admin() as c:c.execute('INSERT INTO auth.sessions(id,user_id) VALUES(%s,%s)',(w.scope.session_id,w.scope.owner_id))


def test_same_input_rekey_returns_original_and_new_start_cannot_use_stale_job(answer_workspace):
    w,j=answer_workspace;r=begin(w,j)
    assert begin(w,j,aid='different-network-request-id')==r
    with tenant_context(w.scope):
        task=w.store.worker_job(j)['task_id']
    with w.admin() as c:c.execute('UPDATE tasks SET generation=generation+1 WHERE task_id=%s',(task,))
    with pytest.raises(psycopg.Error,match='current owned'):begin(w,j,aid='new',input_id='new-input')
    # Accepted work can still settle after its task generation changes.
    assert finish(w)['state']=='settled'


def test_actual_correction_after_job_done_and_zero_actual_refund(answer_workspace):
    w,j=answer_workspace;begin(w,j)
    with w.admin() as c:c.execute("UPDATE worker_jobs SET state='done' WHERE job_id=%s",(j,))
    r=finish(w,u=usage(0,'0'))
    assert r['actual_units']==0 and r['correction_units']==-100000000 and r['overrun'] is False
    assert balance(w)==(1000000000,Decimal(0))


def test_backend_role_two_pipeline_syncs_include_identity_and_commit(answer_workspace,tmp_path):
    w,j=answer_workspace;trace=tmp_path/'answer-protocol.trace'
    # Role SET/claims/BEGIN/COMMIT included. No production pooler or API/runtime claim.
    with psycopg.connect(w.dsn.replace('user=sandbox','user=delivery_app'),autocommit=True) as c,trace.open('wb') as out:
        c.pgconn.trace(out.fileno())
        try:
            for sql,params in [
              ('SELECT requi_answer_start(%s,%s,%s,%s,%s::jsonb)',('traced',j,'input','a'*64,json.dumps(AnswerPricing.from_environment({}).snapshot()))),
              ('SELECT requi_answer_finish(%s,%s::jsonb)',('traced',json.dumps(usage()))),
            ]:
                with c.pipeline():
                    c.execute('BEGIN')
                    c.execute('SET LOCAL ROLE requi_backend')
                    c.execute("SELECT set_config('request.jwt.claim.sub',%s,true),set_config('request.jwt.claim.session_id',%s,true)",(w.scope.owner_id,w.scope.session_id))
                    result=c.execute(sql,params);c.execute('COMMIT')
                assert result.fetchone()[0]['answer_id']=='traced'
        finally:c.pgconn.untrace()
    assert sum('\tF\t' in x and x.endswith('\tSync') for x in trace.read_text().splitlines())==2


def test_final_attempt_uniqueness_rolls_back_correction_across_answers(answer_workspace):
    w,j=answer_workspace;begin(w,j);finish(w)
    begin(w,j,aid='second',input_id='second-input')
    u=usage();u['attempts'].insert(0,{**u['attempts'][0],'attempt_id':'first-in-batch'})
    with pytest.raises(psycopg.errors.UniqueViolation):finish(w,'second',u)
    assert balance(w)==(887500000,Decimal('0.150'))
    with w.admin() as c:
        assert c.execute("SELECT count(*) FROM answer_charge_usage WHERE attempt_id='first-in-batch'").fetchone()==(0,)
        assert c.execute("SELECT state FROM answer_charge_receipts WHERE answer_id='second'").fetchone()==('pending',)


def test_many_calls_have_no_count_gate_and_fractional_answers_share_remainder(answer_workspace):
    w,j=answer_workspace;begin(w,j)
    u=usage(0,'0.003')
    u['attempts']=[{'attempt_id':'bulk-'+str(i),'phase':'intent','input_tokens':0,'output_tokens':0} for i in range(200)]
    first=finish(w,u=u)
    assert first['actual_units']==0 and balance(w)==(1000000000,Decimal('0.150'))
    begin(w,j,aid='fractional-two',input_id='input-two')
    u=usage(0,'0.017');u['attempts'][0]['attempt_id']='fractional-next'
    second=finish(w,'fractional-two',u)
    assert second['actual_units']==1 and balance(w)==(999999999,Decimal(0))


def test_underflow_is_atomic_not_partial_charge(answer_workspace):
    w,j=answer_workspace;begin(w,j)
    with w.admin() as c:
        c.execute('UPDATE credit_accounts SET balance_units=0 WHERE owner_id=%s',(w.scope.owner_id,))
        c.execute('UPDATE credit_accounts SET balance_units=-9223372036854775808 WHERE owner_id=%s',(w.scope.owner_id,))
    with pytest.raises(psycopg.Error,match='integer range'):finish(w,u=usage(100000))
    with w.admin() as c:
        assert c.execute('SELECT count(*) FROM answer_charge_usage WHERE owner_id=%s',(w.scope.owner_id,)).fetchone()==(0,)
        assert c.execute('SELECT state FROM answer_charge_receipts WHERE owner_id=%s',(w.scope.owner_id,)).fetchone()==('pending',)


def test_retry_that_created_a_new_job_still_returns_original_input_receipt(answer_workspace):
    w,j=answer_workspace;r=begin(w,j)
    with tenant_context(w.scope):
        task=w.store.worker_job(j)['task_id']
        second=w.store.enqueue_worker(task,'phase:intent')
    assert begin(w,second,aid='new-job-request')==r
    assert balance(w)==(900000000,Decimal(0))
