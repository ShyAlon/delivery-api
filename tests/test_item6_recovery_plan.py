import sys,copy,hashlib,json
from pathlib import Path
from datetime import datetime,timezone
from decimal import Decimal
import pytest
sys.path.insert(0,str(Path(__file__).parents[1]/'scripts/item6'))
from recovery_plan import accounting_preview
from delivery_api.attempt_ledger import AdmissionDenied
from delivery_api.provider_snapshot import RULE,ProviderSnapshot
from delivery_api.retail_pricing import RetailRates

def fixture():
    start=datetime(2026,10,11,9,41,tzinfo=timezone.utc).timestamp()
    snapshot={'published_period_rule':RULE,'fetched_at':'2026-10-11T03:00:00+00:00','model':'deepseek-flash','model_version':'fixture','parser_version':'deepseek-html-v2',
      'sources':[{'url':'https://api-docs.deepseek.com/'+s,'sha256':'a'*64} for s in ('pricing','api')],
      'published_usd_per_million':{b:{'cache_hit':'0.01','cache_miss':'0.02','output':'0.03'} for b in ('peak','off_peak')}}
    s=ProviderSnapshot(snapshot);r=RetailRates.from_environment({}).snapshot()
    q={'provider':'deepseek','model':'deepseek-flash','price_version':'fixture','bound_version':'fixture','input_max':6000,'output_max':1000,
       'expense_input':20000,'expense_cache_hit':10000,'expense_cache_miss':20000,'expense_output':30000,'expense_units_per_usd':10**12,
       'retail_input':12500,'retail_output':12500,'retail_units_per_credit':10**9,'retail_formula':r,
       'provider_evidence':{'snapshot':snapshot,'snapshot_sha256':s.id}}
    attempt={'attempt_id':'attempt','owner_id':'owner','job_id':'job','state':'uncertain','quote':q,'raw_usage':{'prompt_tokens':5010,'completion_tokens':233,'total_tokens':5243,'prompt_cache_hit_tokens':0,'prompt_cache_miss_tokens':5010},'provider_started_at':start,'raw_usage_received_at':start+2}
    job={'job_id':'job','owner_id':'owner','auth_session_id':'auth','session_id':'engine','task_id':'task','state':'failed'}
    account={'owner_id':'owner','units_per_credit':10**9,'balance_units':1000000000,'fractional_debit_units':Decimal('.25')}
    return attempt,job,account

def test_exact_historic_preview_does_not_mutate():
    args=fixture();before=copy.deepcopy(args);p=accounting_preview(*args)
    assert args==before
    assert p['expense_actual']==107190000
    assert p['provider_usd']=='0.00010719'
    assert p['retail_credits']=='0.0655375'
    assert p['retail_posted_units']==65537500
    assert p['balance_after']==934462500 and Decimal(p['fractional_after'])==Decimal('.25')
    assert p['billed_rate_evidence']['band']=='off_peak'
    assert p['provider_debit_confirmed'] is False

@pytest.mark.parametrize('change',[lambda a,j,c:a.update(state='settled'),lambda a,j,c:j.update(state='running'),lambda a,j,c:a.update(owner_id='foreign'),lambda a,j,c:a.update(raw_usage=None),lambda a,j,c:a.update(raw_usage_received_at=None),lambda a,j,c:c.update(units_per_credit=1000),lambda a,j,c:a.update(expense_actual=0)])
def test_bad_or_unbound_evidence_rejected(change):
    args=fixture();change(*args)
    with pytest.raises((AdmissionDenied,TypeError)):accounting_preview(*args)


def test_copy_strings_and_native_pg_evidence_bind_identically():
    a,j,c=fixture();copy_a=copy.deepcopy(a);copy_j=copy.deepcopy(j);copy_c=copy.deepcopy(c)
    for row in (copy_a,copy_j,copy_c):
        for k,v in list(row.items()):
            if isinstance(v,(int,float,Decimal)):row[k]=str(v)
    assert accounting_preview(a,j,c)==accounting_preview(copy_a,copy_j,copy_c)
