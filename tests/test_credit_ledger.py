"""Credit ledger: the migration, the attribution helper, the history read, the tooltip text and the wiring."""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from delivery_api import credit_audit,credit_history

REPO=Path(__file__).resolve().parents[1]
ROOT=REPO/'src'/'delivery_api'
MIGRATION=REPO/'supabase'/'migrations'/'20261020080000_credit_ledger.sql'


def test_migration_is_append_only_and_forced_row_security():
    sql=MIGRATION.read_text()
    assert 'CREATE TABLE public.credit_ledger' in sql
    assert 'FORCE ROW LEVEL SECURITY' in sql
    assert 'REVOKE ALL ON public.credit_ledger FROM PUBLIC,authenticated' in sql
    assert 'BEFORE UPDATE OR DELETE ON public.credit_ledger' in sql and 'BEFORE TRUNCATE ON public.credit_ledger' in sql
    assert 'AFTER INSERT OR UPDATE ON public.credit_accounts' in sql
    assert 'REFERENCES' not in sql   # audit rows outlive the account
    assert 'GRANT INSERT' not in sql and 'GRANT UPDATE' not in sql and 'GRANT DELETE' not in sql


def test_migration_records_unattributed_changes_instead_of_dropping_them():
    sql=MIGRATION.read_text()
    assert "k:='unattributed'" in sql and "'unattributed:'||session_user" in sql


def test_check_query_is_read_only():
    q=(REPO/'docs'/'credit-ledger-check.sql').read_text()
    body='\n'.join(l for l in q.splitlines() if not l.lstrip().startswith('--')).upper()
    assert body.strip().startswith('SELECT') and not re.search(r'\b(INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|TRUNCATE|GRANT)\b',body)


class Cursor:
    def __init__(self,row=None,rows=None):self.row,self.rows=row,rows
    def fetchone(self):return self.row
    def fetchall(self):return self.rows


def test_attribute_sets_the_four_transaction_settings():
    seen=[]
    def execute(sql,params):seen.append((sql,params));return Cursor()
    credit_audit.attribute(execute,'debit','system:retail_ledger',None,'job1:3')
    sql,params=seen[0]
    assert sql.count("true)")==4 and sql.count('?')==4 and params==('debit','system:retail_ledger','','job1:3')
    credit_audit.attribute(execute,'rescale','x','why',None,marker='%s')
    assert seen[1][0].count('%s')==4


def test_attribute_refuses_an_unknown_kind_or_no_actor():
    with pytest.raises(ValueError):credit_audit.attribute(lambda *a:None,'bonus','a')
    with pytest.raises(ValueError):credit_audit.attribute(lambda *a:None,'grant','')


class HistoryDb:
    def __init__(self,present=True,rows=(),total=0):self.present,self.rows,self.total,self.asked=present,list(rows),total,[]
    def execute(self,sql,params):
        self.asked.append(sql)
        if 'to_regclass' in sql:return Cursor({'present':self.present})
        if 'count(*)' in sql:return Cursor({'n':self.total})
        if "kind IN ('topup','grant')" in sql:
            rows=[r for r in self.rows if r['kind'] in ('topup','grant') and r['delta_units']>0]
            return Cursor(rows[0] if rows else None)
        return Cursor(rows=self.rows)


def row(i,kind,delta,before,after,actor,at=1700000000.0,reason=None,ref=None,per=10**9):
    return {'entry_id':i,'kind':kind,'delta_units':delta,'balance_before_units':before,'balance_after_units':after,'units_per_credit':per,'before_units_per_credit':per,'actor':actor,'reason':reason,'reference':ref,'recorded_at':at}


def test_history_reads_credits_newest_first_and_names_the_source():
    db=HistoryDb(rows=[row(3,'debit',-10**9,40*10**9,39*10**9,'system:retail_ledger',ref='k1'),row(2,'grant',50*10**9,0,50*10**9,'operator:abc',reason='free launch credits')],total=2)
    out=credit_history.read(db)
    assert out['available'] and [e['credits'] for e in out['entries']]==[-1.0,50.0]
    assert out['entries'][1]['by']=='Requi.re operator' and out['entries'][0]['by']=='automatic'
    assert 'abc' not in out['text'] and 'free launch credits' in out['text'] and 'nothing earlier is listed' in out['text']


def test_history_flags_unattributed_changes():
    db=HistoryDb(rows=[row(1,'unattributed',-5,10,5,'unattributed:postgres')],total=1)
    out=credit_history.read(db)
    assert out['entries'][0]['unattributed'] and 'UNATTRIBUTED' in out['text'] and 'postgres' in out['text']


def test_history_without_the_table_says_so_and_does_not_fail():
    out=credit_history.read(HistoryDb(present=False))
    assert out['available'] is False and 'not been set up' in out['text']
    assert credit_history.last_topup(HistoryDb(present=False)) is None


def test_last_topup_is_the_latest_credit_addition_not_a_debit():
    db=HistoryDb(rows=[row(5,'debit',-1,2,1,'system:x'),row(4,'grant',24*10**9,0,24*10**9,'operator:o',at=1700000100.0)])
    assert credit_history.last_topup(db)=={'credits':24.0,'at':1700000100.0,'kind':'grant'}
    assert credit_history.last_topup(HistoryDb(rows=[row(5,'debit',-1,2,1,'system:x')])) is None


def test_history_never_mentions_vendor_cost_or_limits():
    src=(ROOT/'credit_history.py').read_text().lower()
    for word in ('expense','vendor','usd','spend','limit_usd'):assert word not in src


def test_writers_attribute_before_they_change_a_balance():
    for name,needle in (('retail_ledger.py',"'debit','system:retail_ledger'"),('retail_ledger.py',"'rescale','operator:scale_migration'"),('prepaid.py',"'debit','system:prepaid'")):
        src=(ROOT/name).read_text()
        i=src.index(needle);assert 'UPDATE' in src[i:i+700] and 'credit_audit.attribute(' in src[i-60:i]
    grant=(REPO/'scripts'/'ops'/'grant-credits.sh').read_text()
    assert grant.index("set_config('requi.ledger_kind','grant'")<grant.index("UPDATE credit_accounts SET balance_units=balance_units+")


def test_routes_and_store_are_wired():
    app=(ROOT/'app.py').read_text();store=(ROOT/'postgres_store.py').read_text()
    assert "@app.get('/api/account/credit-history')" in app and 'store.own_credit_history()' in app
    assert "out['last_topup']=store.own_last_topup()" in app
    assert 'def own_credit_history(self)' in store and 'def own_last_topup(self)' in store


def run_node(code):
    node=shutil.which('node')
    if not node:pytest.skip('node is not installed')
    tmp=Path(__file__).parent/'_balance_title_probe.mjs'
    tmp.write_text((ROOT/'web'/'account-balance.js').read_text()+'\n'+code)
    try:return subprocess.run([node,str(tmp)],capture_output=True,text=True,timeout=30,check=True).stdout
    finally:tmp.unlink()


def test_tooltip_shows_balance_and_latest_topup():
    out=json.loads(run_node("console.log(JSON.stringify([balanceTitle({credits:3.0266,last_topup:{credits:24,at:1700000000}}),balanceTitle({credits:3,last_topup:null}),balanceTitle({credits:0,last_topup:null}),balanceTitle({credits:null})]))"))
    assert out[0].startswith('Balance 3.03 credits\nLast top-up: +24.00 credits on ') and '2023' in out[0]
    assert out[1]=='Balance 3.00 credits\nNo top-up recorded yet'
    assert 'No credits left' in out[2] and out[3]==''


def test_header_uses_the_tooltip_and_debug_has_the_history():
    js=(ROOT/'web'/'session-cost.js').read_text()
    assert 'el.title=balanceTitle(reply)' in js and "import {balanceLabel,balanceTitle}" in js
    ident=(ROOT/'web'/'identity.js').read_text()
    assert 'id="historyhost"' in ident and "api('/api/account/credit-history')" in ident and "'Credit history',()=>" in ident
    assert ident.index('ledgerwrap')<ident.index('historywrap')<ident.index('Current technical state')
