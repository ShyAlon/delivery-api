import sys,json
from pathlib import Path
from contextlib import contextmanager
import pytest
sys.path.insert(0,str(Path(__file__).parents[1]/'scripts/item6'))
import recovery_phase_failure as m
from delivery_api.tenancy import TenantScope,tenant_context
from delivery_api.attempt_ledger import AdmissionDenied
SCOPE=TenantScope('045bafdf-e35b-4b4b-93f2-0cd981b52806','16caab89-5c55-4c36-af25-ba65105ed74f')

class Cursor:
 def __init__(self,rows):self.rows=rows
 def fetchall(self):return self.rows
 def fetchone(self):return self.rows[0] if self.rows else None

class FakeStore:
 def __init__(self,waived=True):
  self._postgres=self;self.waived=waived;self.provider=False;self.debit=False;self.balance=5362835621;self.authority=m.AUTHORITY
 @contextmanager
 def transaction(self):yield self
 def execute(self,q,args):
  if 'FROM worker_jobs' in q:
   return Cursor([{'job_id':j,'state':s} for j,s in m.ORIGINAL_JOBS.items()]+[{'job_id':m.JOB,'state':'failed','kind':'phase:needs','attempt':1,'generation':1,'session_id':'c8186df5-bfde-4499-86c3-a34823aa7f45','auth_session_id':SCOPE.session_id}])
  if 'FROM provider_attempts' in q:return Cursor([{'one':1}] if self.provider else [])
  if 'FROM retail_contributions' in q:return Cursor([{'one':1}] if self.debit else [])
  if 'FROM credit_accounts' in q:return Cursor([{'balance_units':self.balance,'units_per_credit':10**9}])
  if 'FROM retail_compute_segments' in q:return Cursor([{'state':'waived' if self.waived else 'unresolved','deterministic_ms':None}])
  if 'FROM audit_log' in q:
   return Cursor([{'detail':json.dumps({'authorization_ref':self.authority,'original_dump_sha256':m.ARCHIVE,'measurement':'unknown','disposition':'waived','billed_units':0,'failed_job_unchanged':True})}] if self.waived else [])
  raise AssertionError(q)


def test_exact_no_provider_failure_and_unknown_waiver():
 with tenant_context(SCOPE):
  assert m.validate_phase_failure(FakeStore(),waived=True)['compute_measurement']=='unknown'
  assert m.validate_phase_failure(FakeStore(False),waived=False)['compute_state']=='unresolved'


@pytest.mark.parametrize('field,value',[('provider',True),('debit',True),('balance',0),('authority','changed')])
def test_recovery_rejects_provider_debit_balance_or_authority_drift(field,value):
 store=FakeStore();setattr(store,field,value)
 with tenant_context(SCOPE),pytest.raises(AdmissionDenied):m.validate_phase_failure(store,waived=True)


def test_missing_exact_waiver_authority_cannot_mutate():
 with tenant_context(SCOPE),pytest.raises(AdmissionDenied):m.apply_phase_failure_waiver(FakeStore(False),archive_sha256=m.ARCHIVE,authorization_ref='wrong')
