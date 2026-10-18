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
    def __init__(self,account=None,attempts=(),states=(),charged=(0,0)):
        self.account,self.attempts,self.states,self.charged,self.asked=account,list(attempts),list(states),charged,[]
    def execute(self,sql,params=()):
        self.asked.append((sql,params))
        db=self
        class R:
            def fetchone(s):
                if 'FROM credit_accounts' in sql:return db.account
                if 'FROM retail_contributions' in sql:return {'n':db.charged[0],'posted':db.charged[1]}
            def fetchall(s):
                if 'GROUP BY state' in sql:return db.states
                if 'FROM provider_attempts' in sql:return db.attempts
        return R()


ACCOUNT={'balance_units':5*10**9,'units_per_credit':10**9}


def attempt(n,state='settled',quote=None):
    return {'attempt_id':'a%d'%n,'job_id':'job%08d'%n,'state':state,'created_at':1760796340.0+n,
            'quote':quote if quote is not None else {'provider':'deepseek','model':'deepseek-flash','retail_phase':'intent'}}


def test_an_account_with_no_calls_says_so_and_shows_the_balance():
    r=account_ledger.read(Db(ACCOUNT),A,Decimal('1'))
    assert 'Credit balance: 5.00 credits ($5.00)' in r['text']
    assert 'Provider calls recorded for your account: 0' in r['text'] and 'No provider calls are recorded for your account.' in r['text']
    assert 'Credits charged so far: 0.0000' in r['text'] and r['calls']==0 and r['recent']==[]


def test_no_credit_account_is_zero():
    assert account_ledger.read(Db(None),A,Decimal('1'))['credits']==0.0


def test_calls_are_listed_with_state_provider_model_phase_and_counts():
    db=Db(ACCOUNT,[attempt(2,'settled'),attempt(1,'cancelled')],[{'state':'settled','n':1},{'state':'cancelled','n':1}],(1,250_000_000))
    r=account_ledger.read(db,A,Decimal('1'));t=r['text']
    assert 'Provider calls recorded for your account: 2 (cancelled 1, settled 1)' in t
    assert 'settled   deepseek/deepseek-flash  intent  job job00000' in t and 'cancelled' in t
    assert 'Credits charged so far: 0.2500 (1 charged entries)' in t and r['charged_credits']==0.25


def test_quote_may_be_json_text_or_unreadable():
    r=account_ledger.read(Db(ACCOUNT,[attempt(1,quote=json.dumps({'provider':'deepseek','model':'m'})),attempt(2,quote='not json')]),A,Decimal('1'))
    assert r['recent'][0]['model']=='m' and r['recent'][1]['provider'] is None and '?/?' in r['text']


def test_it_only_reads_this_owner_and_never_writes():
    db=Db(ACCOUNT,[attempt(1)],[{'state':'settled','n':1}])
    account_ledger.read(db,A,Decimal('1'))
    for sql,params in db.asked:
        assert sql.lstrip().upper().startswith('SELECT')
        if 'provider_attempts' in sql or 'credit_accounts' in sql:assert 'WHERE owner_id=?' in sql and params==(A,)
    assert 'LIMIT 20' in [s for s,_ in db.asked if 'ORDER BY' in s][0]


def test_only_listed_columns_are_read_no_prompt_or_answer_text():
    db=Db(ACCOUNT,[attempt(1)])
    account_ledger.read(db,A,Decimal('1'))
    text=' '.join(s for s,_ in db.asked).lower()
    assert 'select *' not in text and 'messages' not in text and 'usage' not in text and 'response' not in text


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
    store._connection=ContextVar('scoped_transaction',default=None);store._db=_Connection(store._connection);store._postgres=Postgres()
    with pytest.raises(TenantAccessDenied):store._db.execute('SELECT 1',())
    with tenant_context(TenantScope(A)):
        out=store.own_ledger(Decimal('1'))
    assert out['calls']==1 and 'Credit balance' in out['text'] and any('SAVEPOINT operation_start' in q for q in conn.sql)


def test_debug_dialog_shows_the_ledger_section_and_loads_it():
    s=(ROOT/'web'/'identity.js').read_text()
    assert 'id="ledgerhost"' in s and "api('/api/account/ledger')" in s and 'Your credits and provider calls' in s
