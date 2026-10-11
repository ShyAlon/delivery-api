import sys,copy
from pathlib import Path
from types import SimpleNamespace
import pytest
sys.path.insert(0,str(Path(__file__).parents[1]/'scripts/item6'))
from recovery_interrupted import validate_interrupted_ledger,ORIGINAL,INTERRUPTED

class Connection:
 def __init__(self,data):self.data=data
 def __enter__(self):return self
 def __exit__(self,*a):pass
 def execute(self,sql,args=()):
  key=next(k for k in self.data if k in sql)
  return SimpleNamespace(fetchall=lambda:self.data[key],fetchone=lambda:self.data[key][0])

def fixture():
 data={'worker_jobs':[{'job_id':j,'state':'failed'} for j in (ORIGINAL,INTERRUPTED)],
 'provider_attempts':[{'state':'settled','job_id':ORIGINAL,'expense_actual':1} for _ in range(67)]+[{'state':'settled','job_id':INTERRUPTED,'expense_actual':expense} for expense in [52842000, 135492000, 893118000, 155118000, 181968000, 964950000, 1046250000, 880200000, 954450000]],
 'retail_compute_segments':[{'job_id':ORIGINAL,'state':'waived','deterministic_ms':None},{'job_id':INTERRUPTED,'state':'unresolved','deterministic_ms':None}],
 'credit_accounts':[{'balance_units':3195487500,'units_per_credit':1000000000}]}
 store=SimpleNamespace(_postgres=SimpleNamespace(transaction=lambda:Connection(data)),audit_entries=lambda tid:[{'action':'item6.provider.reconciled'},{'action':'item6.continuation.queued','detail':{'original_failed_job':ORIGINAL,'new_job_id':INTERRUPTED}}])
 return data,store

def test_exact_interruption_ledger_and_budget():
 data,store=fixture();r=validate_interrupted_ledger(store)
 # Diagnostic count: original66 settlements + reconciled1, then9 new=76.
 assert len([r for r in data['provider_attempts'] if r['job_id']==ORIGINAL])==67
 assert len([r for r in data['provider_attempts'] if r['job_id']==INTERRUPTED])==9
 assert r['remaining_provider_cap_units']==994735612000 and r['balance_units']==3195487500

@pytest.mark.parametrize('change',[lambda d:d['provider_attempts'][0].update(state='uncertain'),lambda d:d['worker_jobs'][1].update(state='running'),lambda d:d['credit_accounts'][0].update(balance_units=4),lambda d:d['provider_attempts'][-1].update(expense_actual=0),lambda d:d['retail_compute_segments'][1].update(deterministic_ms=0)])
def test_changed_interruption_ledger_rejected(change):
 data,store=fixture();change(data)
 with pytest.raises(ValueError):validate_interrupted_ledger(store)
