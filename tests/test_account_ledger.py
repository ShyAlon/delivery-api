"""Own-account ledger view: the read, its text, the route, the store method with the real scope guard, and the Debug dialog wiring."""
import json
from contextlib import contextmanager
from contextvars import ContextVar
from decimal import Decimal
from pathlib import Path

import pytest

from delivery_api import account_ledger

ROOT=Path(__file__).resolve().parents[1]/'src'/'delivery_api'
A='11111111-1111-4111-8111-111111111111'


class Db:
    """Canned rows, keyed by what the query reads. Remembers every statement so the tests can prove it only reads."""
    def __init__(self,account=None,attempts=(),states=(),kinds=(),debits=(),segments=(),seg_attempts=(),seg_tokens=(),seg_nonterminal=()):
        self.account,self.attempts,self.states,self.kinds,self.debits,self.asked=account,list(attempts),list(states),list(kinds),list(debits),[]
        self.segments,self.seg_attempts,self.seg_tokens,self.seg_nonterminal=list(segments),list(seg_attempts),list(seg_tokens),list(seg_nonterminal)
    def execute(self,sql,params=()):
        self.asked.append((sql,params))
        db=self
        class R:
            def fetchone(s):
                if 'FROM credit_accounts' in sql:return db.account
            def fetchall(s):
                if 'FROM retail_compute_segments' in sql:return db.segments
                if "kind='tokens'" in sql:return db.seg_tokens
                if "state IN ('reserved','dispatched','uncertain','held')" in sql:return db.seg_nonterminal
                if 'GROUP BY job_id' in sql:return db.seg_attempts
                if 'GROUP BY state' in sql:return db.states
                if 'GROUP BY kind' in sql:return db.kinds
                if 'FROM retail_contributions' in sql:return db.debits
                if 'FROM provider_attempts' in sql:return db.attempts
        return R()


ACCOUNT={'balance_units':5*10**9,'units_per_credit':10**9}


def debit(n,kind='compute',units=100_000,at=1760796340.0,extra=None):
    r={'contribution_key':'k%d'%n,'owner_id':A,'job_id':'job%08d'%n,'phase_key':'probing','kind':kind,'posted_units':units,'exact_units':Decimal(units),'created_at':at+n,'fingerprint':'f','rates':{'x':1}}
    r.update(extra or {});return r


def attempt(n,state='settled',quote=None):
    return {'attempt_id':'a%d'%n,'job_id':'job%08d'%n,'state':state,'created_at':1760796340.0+n,
            'quote':quote if quote is not None else {'provider':'deepseek','model':'deepseek-flash','retail_phase':'intent'}}


def test_an_account_with_no_calls_says_so_and_shows_the_balance():
    r=account_ledger.read(Db(ACCOUNT),A,Decimal('1'))
    assert 'Credit balance: 5.0000 credits ($5.0000)' in r['text']
    assert 'Provider calls recorded for your account: 0' in r['text'] and 'No provider calls are recorded for your account.' in r['text']
    assert 'Credits debited (tokens + compute): 0.000000 in 0 entries' in r['text'] and 'No debits are recorded for your account.' in r['text'] and r['calls']==0 and r['recent']==[] and r['debits']==[]


def test_no_credit_account_is_zero():
    assert account_ledger.read(Db(None),A,Decimal('1'))['credits']==0.0


def test_calls_are_listed_with_state_provider_model_phase_and_counts():
    db=Db(ACCOUNT,[attempt(2,'settled'),attempt(1,'cancelled')],[{'state':'settled','n':1},{'state':'cancelled','n':1}],[{'kind':'tokens','n':1,'posted':250_000_000}])
    r=account_ledger.read(db,A,Decimal('1'));t=r['text']
    assert 'Provider calls recorded for your account: 2 (cancelled 1, settled 1)' in t
    assert 'settled   deepseek/deepseek-flash  intent  job job00000' in t and 'cancelled' in t
    assert 'Credits debited (tokens + compute): 0.250000 in 1 entries (tokens 0.250000)' in t and r['charged_credits']==0.25


def test_a_small_debit_is_visible_with_kind_phase_job_and_the_balance_shows_four_decimals():
    # Shy's live case: balance 5 minus one 0.0001 compute debit must not display as 5.00.
    acct={'balance_units':4_999_900_000,'units_per_credit':10**9}
    db=Db(acct,kinds=[{'kind':'compute','n':1,'posted':100_000}],debits=[debit(1,'compute',100_000)])
    r=account_ledger.read(db,A,Decimal('1'));t=r['text']
    assert 'Credit balance: 4.9999 credits ($4.9999)' in t
    assert 'Credits debited (tokens + compute): 0.000100 in 1 entries (compute 0.000100)' in t
    assert 'compute  probing  job job00000  0.000100 credits' in t and 'No provider calls are recorded for your account.' in t
    assert r['debits']==[{'time':'2025-10-18 14:05:41Z','kind':'compute','phase':'probing','job':'job00000','credits':0.0001}]


def test_debits_are_newest_first_capped_and_only_fixed_fields_leave_the_module():
    rows=[debit(n,'tokens' if n%2 else 'compute',1000,extra={'secret_note':'must not appear'}) for n in range(30)]
    r=account_ledger.read(Db(ACCOUNT,kinds=[{'kind':'tokens','n':15,'posted':15000},{'kind':'compute','n':15,'posted':15000}],debits=rows),A,Decimal('1'))
    assert len(r['debits'])==20 and r['debits'][0]['job']=='job00000'.replace('0'*8,'%08d'%29) and r['debits'][0]['time']>r['debits'][1]['time']
    assert set(r['debits'][0])=={'time','kind','phase','job','credits'} and 'must not appear' not in r['text'] and 'fingerprint' not in r['text']
    assert '(compute 0.000015, tokens 0.000015)' in r['text']


def test_a_debit_table_without_a_time_column_still_lists_entries():
    rows=[debit(1,extra={'created_at':None})]
    r=account_ledger.read(Db(ACCOUNT,kinds=[{'kind':'compute','n':1,'posted':1000}],debits=rows),A,Decimal('1'))
    assert '(time unknown)' in r['text'] and r['debits'][0]['time'] is None


def test_quote_may_be_json_text_or_unreadable():
    r=account_ledger.read(Db(ACCOUNT,[attempt(1,quote=json.dumps({'provider':'deepseek','model':'m'})),attempt(2,quote='not json')]),A,Decimal('1'))
    assert r['recent'][0]['model']=='m' and r['recent'][1]['provider'] is None and '?/?' in r['text']


def test_it_only_reads_this_owner_and_never_writes():
    db=Db(ACCOUNT,[attempt(1)],[{'state':'settled','n':1}])
    account_ledger.read(db,A,Decimal('1'))
    for sql,params in db.asked:
        assert sql.lstrip().upper().startswith('SELECT')
        if 'provider_attempts' in sql or 'credit_accounts' in sql:assert 'WHERE owner_id=?' in sql and params==(A,)
    assert any('retail_contributions' in s for s,_ in db.asked)
    assert 'LIMIT 20' in [s for s,_ in db.asked if 'ORDER BY' in s][0]


def test_only_listed_columns_are_read_no_prompt_or_answer_text():
    db=Db(ACCOUNT,[attempt(1)])
    account_ledger.read(db,A,Decimal('1'))
    text=' '.join(s for s,_ in db.asked if 'retail_contributions' not in s).lower()
    assert 'select *' not in text and 'messages' not in text and 'usage' not in text and 'response' not in text
    # The one wildcard read is the numeric debit table; its output is cut to fixed fields (see the fixed-fields test above).
    star=[s for s,_ in db.asked if 'select *' in s.lower()]
    assert len(star)==1 and 'FROM retail_contributions' in star[0]


def test_invalid_scale_is_refused():
    with pytest.raises(ValueError):account_ledger.read(Db({'balance_units':1,'units_per_credit':0}),A,Decimal('1'))


def test_route_is_private_goes_through_the_store_and_local_mode_says_unavailable():
    s=(ROOT/'app.py').read_text()
    body=s.split("@app.get('/api/account/ledger')")[1].split('\n\n')[0]
    assert 'Depends(auth.require_actor)' in body and 'store.own_ledger(' in body and 'store._db' not in body
    assert 'if not config.CLOUD_TENANTS:return {' in body and 'text' in body


def test_store_method_runs_in_a_scoped_transaction():
    from delivery_api.postgres_store import PostgresStore,_Connection
    from delivery_api.tenancy import TenantAccessDenied,TenantScope,tenant_context
    inner=Db(ACCOUNT,[attempt(1)],[{'state':'settled','n':1}])
    class Cursor:
        def __init__(self,r):self.r,self.rowcount=r,1
        def fetchone(self):return self.r.fetchone()
        def fetchall(self):return self.r.fetchall()
    class Conn:
        def __init__(self):self.sql=[]
        def execute(self,sql,params=()):
            self.sql.append(sql);return Cursor(inner.execute(sql,params))
    conn=Conn()
    class Postgres:
        @contextmanager
        def transaction(self,**kw):yield conn
    store=PostgresStore.__new__(PostgresStore)
    store._connection=ContextVar('scoped_transaction',default=None);store._db=_Connection(store._connection,store);store._postgres=Postgres();store._cache_dirty=ContextVar('cache_dirty',default=False)
    with pytest.raises(TenantAccessDenied):store._db.execute('SELECT 1',())
    with tenant_context(TenantScope(A)):
        out=store.own_ledger(Decimal('1'))
    assert out['calls']==1 and 'Credit balance' in out['text'] and any('SAVEPOINT operation_start' in q for q in conn.sql)


def test_debug_dialog_shows_the_ledger_section_and_loads_it():
    s=(ROOT/'web'/'identity.js').read_text()
    assert 'id="ledgerhost"' in s and "api('/api/account/ledger')" in s and 'Your credits and provider calls' in s


def segment(n,state='unresolved',job_state='failed',phase='intent'):
    return {'segment_key':'job%08d:compute'%n,'job_id':'job%08d'%n,'phase_key':phase,'state':state,'job_state':job_state}


def test_stuck_compute_records_are_listed_with_what_blocks_and_what_can_be_released():
    # Shy's live case: two failed Starts left unresolved compute records and no provider call, which blocks every later Start.
    db=Db(ACCOUNT,segments=[segment(2),segment(1),segment(3,'settled','done')])
    r=account_ledger.read(db,A,Decimal('1'));t=r['text']
    assert 'Compute records: 3, 2 not settled, 2 block new work, 2 can be released below.' in t
    assert 'unresolved' in t and 'blocks new work: yes  releasable: yes' in t and t.count('provider calls 0')==2
    assert [s['releasable'] for s in r['segments']]==[True,True] and all(s['blocks_admission'] for s in r['segments'])
    assert 'job00000003' not in t and set(r['segments'][0])=={'key','job','phase','state','job_state','provider_calls','blocks_admission','releasable'}


def test_a_record_with_a_provider_call_still_in_flight_blocks_but_cannot_be_released():
    db=Db(ACCOUNT,segments=[segment(1)],seg_attempts=[{'job_id':'job00000001','n':1}],seg_nonterminal=[{'job_id':'job00000001','n':1}])
    r=account_ledger.read(db,A,Decimal('1'))
    assert r['segments'][0]['blocks_admission'] is True and r['segments'][0]['releasable'] is False and 'releasable: no' in r['text']


def test_a_crashed_record_whose_provider_calls_all_settled_can_be_released():
    # The blocked-interview shape: the job died after both calls settled, so the
    # charge is final; the owner releases it and admission unblocks.
    db=Db(ACCOUNT,segments=[segment(1)],seg_attempts=[{'job_id':'job00000001','n':2}],seg_tokens=[{'job_id':'job00000001','n':1}])
    r=account_ledger.read(db,A,Decimal('1'))
    assert r['segments'][0]['blocks_admission'] is True and r['segments'][0]['releasable'] is True and 'releasable: yes' in r['text']


def test_a_record_of_a_running_job_neither_blocks_nor_releases():
    r=account_ledger.read(Db(ACCOUNT,segments=[segment(1,'pending','running')]),A,Decimal('1'))
    assert r['segments'][0]['blocks_admission'] is False and r['segments'][0]['releasable'] is False


def test_no_or_settled_records_are_stated_plainly():
    assert 'Compute records: none.' in account_ledger.read(Db(ACCOUNT),A,Decimal('1'))['text']
    r=account_ledger.read(Db(ACCOUNT,segments=[segment(1,'settled','done')]),A,Decimal('1'))
    assert 'Compute records: 1, all settled.' in r['text'] and r['segments']==[]


def test_segment_reads_are_scoped_to_the_owner_and_only_select():
    db=Db(ACCOUNT)
    account_ledger.read(db,A,Decimal('1'))
    seg=[(s,p) for s,p in db.asked if 'retail_compute_segments' in s]
    assert len(seg)==1 and 'WHERE s.owner_id=?' in seg[0][0] and seg[0][1]==(A,) and seg[0][0].lstrip().upper().startswith('SELECT')


def test_release_route_is_private_calls_the_store_and_confirms_before_writing():
    s=(ROOT/'app.py').read_text()
    body=s.split("@app.post('/api/account/compute-release')")[1].split('\n\n\n')[0]
    assert 'Depends(auth.require_actor)' in body and 'store.release_own_compute(body.confirm)' in body and 'store._db' not in body
    assert "if not config.CLOUD_TENANTS:raise HTTPException(404" in body and "out['status']='preview'" in body


def test_debug_dialog_offers_the_release_only_when_something_is_releasable_and_asks_first():
    s=(ROOT/'web'/'identity.js').read_text()
    assert 'id="computefix"' in s and "filter(s=>s.releasable)" in s and "'/api/account/compute-release'" in s
    assert s.index('confirm:false')<s.index('window.confirm(p.message)')<s.index('confirm:true')
