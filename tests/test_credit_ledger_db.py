"""The credit ledger on a real Postgres: what the trigger records, who can read it, and that nobody can rewrite it."""
import os
import uuid

import pytest

DSN=os.getenv('DELIVERY_TEST_POSTGRES_DSN')
pytestmark=pytest.mark.skipif(not DSN,reason='requires isolated migrated Postgres')
SCALE=10**9


@pytest.fixture
def owner():
    import psycopg
    oid=str(uuid.uuid4())
    with psycopg.connect(DSN,autocommit=True) as c:
        c.execute("INSERT INTO auth.users(id,email,email_confirmed_at) VALUES(%s,%s,now())",(oid,oid+'@example.test'))
        c.execute("INSERT INTO credit_accounts(owner_id,balance_units,units_per_credit,updated_at) VALUES(%s,0,%s,0)",(oid,SCALE))
    yield oid
    with psycopg.connect(DSN,autocommit=True) as c:
        c.execute('DELETE FROM credit_accounts WHERE owner_id=%s',(oid,))


def conn():
    import psycopg
    return psycopg.connect(DSN,autocommit=True)


def ledger(c,oid):
    return c.execute("SELECT kind,delta_units,balance_before_units,balance_after_units,actor,reason,reference,before_units_per_credit,units_per_credit FROM credit_ledger WHERE owner_id=%s ORDER BY entry_id",(oid,)).fetchall()


def attribute(c,kind,actor,reason,reference):
    c.execute("SELECT set_config('requi.ledger_kind',%s,true),set_config('requi.ledger_actor',%s,true),set_config('requi.ledger_reason',%s,true),set_config('requi.ledger_reference',%s,true)",(kind,actor,reason,reference))


def test_a_new_empty_account_writes_nothing(owner):
    with conn() as c:assert ledger(c,owner)==[]


def test_grant_is_recorded_with_who_why_and_before_after(owner):
    with conn() as c,c.transaction():
        attribute(c,'grant','operator:op-1','free launch credits','key-1')
        c.execute('UPDATE credit_accounts SET balance_units=balance_units+%s,updated_at=1 WHERE owner_id=%s',(50*SCALE,owner))
        rows=ledger(c,owner)
    assert rows==[('grant',50*SCALE,0,50*SCALE,'operator:op-1','free launch credits','key-1',SCALE,SCALE)]


def test_a_change_with_no_attribution_is_recorded_as_unattributed_and_settings_do_not_leak(owner):
    with conn() as c:
        with c.transaction():
            attribute(c,'grant','operator:op-1','why','k')
            c.execute('UPDATE credit_accounts SET balance_units=balance_units+10 WHERE owner_id=%s',(owner,))
        c.execute('UPDATE credit_accounts SET balance_units=balance_units-4 WHERE owner_id=%s',(owner,))
        last=ledger(c,owner)[-1]
    assert last[0]=='unattributed' and last[1]==-4 and last[4].startswith('unattributed:') and last[5] is None


def test_debit_is_negative_and_updates_that_move_no_credits_write_nothing(owner):
    with conn() as c:
        c.execute('UPDATE credit_accounts SET balance_units=100 WHERE owner_id=%s',(owner,))
        with c.transaction():
            attribute(c,'debit','system:retail_ledger','','job:1')
            c.execute('UPDATE credit_accounts SET balance_units=balance_units-30 WHERE owner_id=%s',(owner,))
        n=len(ledger(c,owner))
        c.execute('UPDATE credit_accounts SET updated_at=9,fractional_debit_units=0.5 WHERE owner_id=%s',(owner,))
        rows=ledger(c,owner)
    assert rows[-1][:4]==('debit',-30,100,70) and len(rows)==n


def test_scale_change_is_a_rescale_that_moves_no_credits(owner):
    with conn() as c:
        c.execute('UPDATE credit_accounts SET balance_units=0,units_per_credit=1000 WHERE owner_id=%s',(owner,))
        c.execute('UPDATE credit_accounts SET balance_units=5 WHERE owner_id=%s',(owner,))
        with c.transaction():
            attribute(c,'debit','operator:scale_migration','scale','')
            c.execute('UPDATE credit_accounts SET balance_units=5*%s::bigint,units_per_credit=%s WHERE owner_id=%s',(SCALE//1000,SCALE,owner))
        row=ledger(c,owner)[-1]
    assert row[0]=='rescale' and row[1]==0 and row[7]==1000 and row[8]==SCALE


def test_nobody_can_rewrite_the_ledger_even_the_database_owner(owner):
    import psycopg
    with conn() as c:
        with c.transaction():
            attribute(c,'grant','operator:op-1','x','k')
            c.execute('UPDATE credit_accounts SET balance_units=balance_units+1 WHERE owner_id=%s',(owner,))
        for sql in ("UPDATE credit_ledger SET reason='x'","DELETE FROM credit_ledger","TRUNCATE credit_ledger"):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):c.execute(sql)


def test_owner_reads_only_own_rows_and_the_app_role_cannot_write_the_ledger(owner):
    import psycopg
    other=str(uuid.uuid4())
    with conn() as c:
        c.execute("INSERT INTO auth.users(id,email,email_confirmed_at) VALUES(%s,%s,now())",(other,other+'@example.test'))
        c.execute("INSERT INTO auth.sessions(id,user_id) VALUES(%s,%s)",(other,other))
        c.execute("INSERT INTO auth.sessions(id,user_id) VALUES(%s,%s)",(owner,owner))
        with c.transaction():
            attribute(c,'grant','operator:op-1','x','k')
            c.execute('UPDATE credit_accounts SET balance_units=balance_units+7 WHERE owner_id=%s',(owner,))
        def as_backend(uid,fn):
            with c.transaction():
                c.execute('SET LOCAL ROLE requi_backend')
                c.execute("SELECT set_config('request.jwt.claim.sub',%s,true),set_config('request.jwt.claim.session_id',%s,true)",(uid,uid))
                return fn()
        try:
            mine=as_backend(owner,lambda:c.execute('SELECT count(*) FROM credit_ledger').fetchone()[0])
            theirs=as_backend(other,lambda:c.execute('SELECT count(*) FROM credit_ledger WHERE owner_id=%s',(owner,)).fetchone()[0])
            assert mine>=1 and theirs==0
            with pytest.raises(psycopg.errors.Error):
                as_backend(owner,lambda:c.execute("INSERT INTO credit_ledger(owner_id,kind,delta_units,balance_before_units,balance_after_units,units_per_credit,before_units_per_credit,actor,db_role,txid,recorded_at) VALUES(%s,'grant',1,0,1,1,1,'x','x',1,1)",(owner,)))
            def backend_debit():
                attribute(c,'debit','system:retail_ledger','','k9')
                c.execute('UPDATE credit_accounts SET balance_units=balance_units-1 WHERE owner_id=%s',(owner,))
            as_backend(owner,backend_debit)
            assert ledger(c,owner)[-1][:3]==('debit',-1,7)   # the app role has no INSERT right on the ledger, the trigger writes it
        finally:
            c.execute('DELETE FROM auth.sessions WHERE id IN (%s,%s)',(owner,other))
