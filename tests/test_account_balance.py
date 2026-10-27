"""Customer credit balance: the read, the route wiring, the header text and the stylesheet rules."""
import json
import shutil
import subprocess
from decimal import Decimal
from pathlib import Path

import pytest

from delivery_api import account_balance

ROOT=Path(__file__).resolve().parents[1]/'src'/'delivery_api'


class Db:
    def __init__(self,rows):self.rows,self.asked=rows,[]
    def execute(self,sql,params):
        self.asked.append((sql,params));owner=params[0]
        class R:
            def fetchone(s):return self.rows.get(owner)
        return R()


def test_reads_balance_in_credits_and_usd():
    db=Db({'o1':{'balance_units':5*10**9,'units_per_credit':10**9}})
    assert account_balance.read(db,'o1',Decimal('1')) == {'credits':5.0,'usd':5.0}


def test_reads_only_the_asked_owner():
    db=Db({'o1':{'balance_units':5*10**9,'units_per_credit':10**9},'o2':{'balance_units':9*10**9,'units_per_credit':10**9}})
    account_balance.read(db,'o1',Decimal('1'))
    assert db.asked[0][1]==('o1',) and 'WHERE owner_id=?' in db.asked[0][0]


def test_no_account_is_zero_credits():
    assert account_balance.read(Db({}),'nobody',Decimal('1'))=={'credits':0.0,'usd':0.0}


def test_fractional_and_negative_balances_are_exact():
    assert account_balance.read(Db({'o':{'balance_units':1_250_000_000,'units_per_credit':10**9}}),'o',Decimal('1'))['credits']==1.25
    assert account_balance.read(Db({'o':{'balance_units':-500_000_000,'units_per_credit':10**9}}),'o',Decimal('1'))['credits']==-0.5


def test_usd_follows_the_retail_rate():
    assert account_balance.read(Db({'o':{'balance_units':2*10**9,'units_per_credit':10**9}}),'o',Decimal('0.5'))['usd']==1.0


def test_invalid_scale_is_refused_not_guessed():
    with pytest.raises(ValueError):account_balance.read(Db({'o':{'balance_units':1,'units_per_credit':0}}),'o',Decimal('1'))


def test_route_is_private_and_local_mode_has_no_balance():
    s=(ROOT/'app.py').read_text()
    body=s.split("@app.get('/api/account/credits')")[1].split('\n\n')[0]
    assert 'Depends(auth.require_actor)' in body and 'if not config.CLOUD_TENANTS:return {\'credits\':None' in body
    # The database is only reachable through a store method (its scoped transaction); a direct store._db read is the 404 this fixed.
    assert 'store.own_credit_balance(' in body and 'store._db' not in body


def test_store_method_reads_inside_a_scoped_transaction_and_a_direct_read_is_refused():
    from contextlib import contextmanager
    from contextvars import ContextVar
    from delivery_api.postgres_store import PostgresStore,_Connection
    from delivery_api.tenancy import TenantAccessDenied,TenantScope,tenant_context

    class Cursor:
        def __init__(self,row):self.row=row;self.rowcount=1
        def fetchone(self):return self.row

    class Conn:
        def __init__(self):self.sql=[]
        def execute(self,sql,params=()):
            self.sql.append(sql)
            return Cursor({'balance_units':5*10**9,'units_per_credit':10**9} if 'credit_accounts' in sql else None)
    conn=Conn()

    class Postgres:
        @contextmanager
        def transaction(self,**kwargs):yield conn
    store=PostgresStore.__new__(PostgresStore)
    store._connection=ContextVar('scoped_transaction',default=None);store._db=_Connection(store._connection,store);store._postgres=Postgres();store._cache_dirty=ContextVar('cache_dirty',default=False)
    with pytest.raises(TenantAccessDenied):store._db.execute('SELECT balance_units FROM credit_accounts WHERE owner_id=?',('o',))
    with tenant_context(TenantScope('11111111-1111-4111-8111-111111111111')):
        assert store.own_credit_balance(Decimal('1'))=={'credits':5.0,'usd':5.0}
    assert any('SAVEPOINT operation_start' in q for q in conn.sql) and any('credit_accounts' in q for q in conn.sql)


def run_node(code):
    node=shutil.which('node')
    if not node:pytest.skip('node is not installed')
    tmp=Path(__file__).parent/'_balance_probe.mjs'
    tmp.write_text((ROOT/'web'/'account-balance.js').read_text()+'\n'+code)
    try:return subprocess.run([node,str(tmp)],capture_output=True,text=True,timeout=30,check=True).stdout
    finally:tmp.unlink()


def test_header_text_for_balance_values():
    out=json.loads(run_node("console.log(JSON.stringify([balanceLabel({credits:5}),balanceLabel({credits:0}),balanceLabel({credits:null}),balanceLabel(null),balanceLabel({credits:1234.5})]))"))
    assert out[0]=='Balance 5.00 credits' and out[1]=='Balance 0.00 credits' and out[2] is None and out[3] is None and out[4].startswith('Balance 1,234.50')


def test_header_refuses_a_non_number():
    node=shutil.which('node')
    if not node:pytest.skip('node is not installed')
    with pytest.raises(subprocess.CalledProcessError):run_node("balanceLabel({credits:'x'})")


def test_session_cost_text_is_untouched_and_balance_is_wired():
    js=(ROOT/'web'/'session-cost.js').read_text()
    assert "${value.price.toLocaleString(undefined,{minimumFractionDigits:2,maximumFractionDigits:2})} credits · $${value.price_usd.toFixed(2)}" in js
    assert "'/api/account/credits'" in js and 'stopBalance()' in js and "No Session Selected" in js
    css=(ROOT/'web'/'style.css').read_text()
    assert '.session-cost.balance:not([hidden])+.session-cost{margin-left:0}' in css and 'content:"Session cost: "' in css
