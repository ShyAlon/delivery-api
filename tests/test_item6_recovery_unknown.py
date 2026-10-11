import sys,uuid,time
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).parents[1]/'scripts/item6'))
from test_attempt_ledger import ledger,Q
from test_postgres_store import DSN,A,B,store
from delivery_api.tenancy import tenant_context
from delivery_api.attempt_ledger import AdmissionDenied
from recovery_unknown import waive_unknown_provider,ATTEMPT,JOB,ARCHIVE
from recovery_budget import RecoveryBudgetGateway
pytestmark=pytest.mark.skipif(not DSN,reason='disposable PG required')

def test_unknown_failure_waiver_not_settlement_no_debit_cap_still_consumed(ledger,monkeypatch):
 import recovery_unknown as m
 s,tid,job=ledger
 monkeypatch.setattr(m,'AUTHORITY','test-only-original-message');monkeypatch.setattr(m,'JOB',job);monkeypatch.setattr(m,'ATTEMPT',uuid.uuid4().hex);attempt=m.ATTEMPT
 quote=Q|{'expense_input':2139480000,'expense_output':1,'input_max':10,'output_max':1,'retail_units_per_credit':10**9}
 import psycopg
 with psycopg.connect(DSN) as c:
  c.execute('UPDATE admission_limits SET expense_limit=100000000000,attempt_expense_max=100000000000 WHERE owner_id=%s',(A.owner_id,))
  c.execute('INSERT INTO credit_accounts(owner_id,balance_units,units_per_credit,updated_at) VALUES(%s,362835621,1000000000,%s) ON CONFLICT(owner_id) DO UPDATE SET balance_units=362835621,units_per_credit=1000000000',(A.owner_id,time.time()))
 with tenant_context(A):
  s.reserve_provider_attempt(job,attempt,quote,'unknown',require_prepaid=True);s.transition_provider_attempt(attempt,'start',require_prepaid=True);s.transition_provider_attempt(attempt,'uncertain',require_prepaid=True)
  s.finish_worker(job,failed=True)
  # Exact preserved reservation, unknown measured usage and unchanged credit.
  with s._postgres.transaction() as c:c.execute('UPDATE provider_attempts SET expense_reserved=21394800000 WHERE attempt_id=%s',(attempt,))
  result=waive_unknown_provider(s,authorization_ref='test-only-original-message',archive_sha256=ARCHIVE)
  assert result['provider_expense_actual'] is None and result['retail_debit_units']==0
  assert s.provider_attempt(attempt)['state']=='failure_waived'
  assert all(s.provider_attempt(attempt)[k] is None for k in ('raw_usage','usage','expense_actual','retail_actual','settled_at'))
  with s._postgres.transaction() as c:
   assert c.execute('SELECT balance_units FROM credit_accounts').fetchone()['balance_units']==362835621
   assert c.execute('SELECT charged_units FROM credit_charges WHERE attempt_id=%s',(attempt,)).fetchone()['charged_units'] is None
  assert waive_unknown_provider(s,authorization_ref='test-only-original-message',archive_sha256=ARCHIVE)['replayed']
  with pytest.raises(AdmissionDenied):waive_unknown_provider(s,authorization_ref='changed',archive_sha256=ARCHIVE)

 with psycopg.connect(DSN) as c:
  c.execute('DELETE FROM credit_charges WHERE attempt_id=%s',(attempt,))
  c.execute('DELETE FROM credit_accounts WHERE owner_id=%s',(A.owner_id,))


def test_wire_audit_private_bound_to_attempt_and_token_only(ledger):
 from delivery_api.producer_admission import WorkerAdmissionGateway
 s,tid,job=ledger;attempt=uuid.uuid4().hex
 data={'status':'failed','response_sha256':'a'*64,'response_bytes':50,'cr_count':2,'lf_count':0,'event_count':1,'terminal_usage':{'prompt_tokens':10,'completion_tokens':2},'done_seen':False,'error_class':'RuntimeError'}
 with tenant_context(A):
  s.reserve_provider_attempt(job,attempt,Q,'wire')
  gateway=WorkerAdmissionGateway(s,job)
  gateway.record_wire_evidence(attempt,data)
  with pytest.raises(AdmissionDenied):gateway.record_wire_evidence(attempt,data|{'terminal_usage':{'prompt':'private'}})
  with pytest.raises(AdmissionDenied):gateway.record_wire_evidence('wrong',data)
  with tenant_context(B):
   with pytest.raises(AdmissionDenied):gateway.record_wire_evidence(attempt,data)
