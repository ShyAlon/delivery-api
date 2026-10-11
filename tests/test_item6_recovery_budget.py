import sys,uuid
from pathlib import Path
from types import SimpleNamespace
import pytest
sys.path.insert(0,str(Path(__file__).parents[1]/'scripts/item6'))
from test_attempt_ledger import ledger,Q
from test_postgres_store import DSN,A,B,store
from delivery_api.tenancy import tenant_context
from delivery_api.attempt_ledger import AdmissionDenied
from recovery_budget import RecoveryBudgetGateway
pytestmark=pytest.mark.skipif(not DSN,reason='isolated PG required')

def test_run_cap_reservation_atomic_and_meter_forwarded(ledger):
    s,tid,job=ledger
    class Inner:
        store=s;compute_meter=None
        def reserve_serialized(self,attempt_id):return s.reserve_provider_attempt(job,attempt_id,Q,'test')['attempt_id']
    inner=Inner();g=RecoveryBudgetGateway(inner,original_job_ids=[],maximum_expense_units=30)
    meter=object();g.compute_meter=meter
    assert inner.compute_meter is meter
    with tenant_context(A):
        first=uuid.uuid4().hex;second=uuid.uuid4().hex
        assert g.reserve_serialized(attempt_id=first)==first
        with pytest.raises(AdmissionDenied,match='cap'):g.reserve_serialized(attempt_id=second)
        assert s.provider_attempt(second) is None  # rolled back, never dispatched
        s.transition_provider_attempt(first,'cancel')
        assert g.reserve_serialized(attempt_id=second)==second
        s.transition_provider_attempt(second,'cancel')


def test_reentry_keeps_previous_continuation_expense_in_cumulative_cap(ledger):
    s,tid,job=ledger
    class Inner:
        store=s
        def reserve_serialized(self,attempt_id):return s.reserve_provider_attempt(job,attempt_id,Q,'new')['attempt_id']
    with tenant_context(A):
        earlier=uuid.uuid4().hex;s.reserve_provider_attempt(job,earlier,Q,'old');s.transition_provider_attempt(earlier,'start');s.transition_provider_attempt(earlier,'settle',{'input_tokens':2,'output_tokens':3})
        g=RecoveryBudgetGateway(Inner(),original_job_ids=['original-failed-not-the-interrupted-job'],maximum_expense_units=37)
        nxt=uuid.uuid4().hex
        with pytest.raises(AdmissionDenied,match='cap'):g.reserve_serialized(attempt_id=nxt)
        assert s.provider_attempt(nxt) is None


def test_waived_unknown_reservation_stays_in_cumulative_cap(ledger):
 s,tid,job=ledger
 class Inner:
  store=s
  def reserve_serialized(self,attempt_id):return s.reserve_provider_attempt(job,attempt_id,Q,'new')['attempt_id']
 with tenant_context(A):
  earlier=uuid.uuid4().hex;s.reserve_provider_attempt(job,earlier,Q,'old')
  with s._postgres.transaction() as c:c.execute("UPDATE provider_attempts SET state='failure_waived' WHERE attempt_id=%s",(earlier,))
  g=RecoveryBudgetGateway(Inner(),original_job_ids=[],maximum_expense_units=59)
  nxt=uuid.uuid4().hex
  with pytest.raises(AdmissionDenied,match='cap'):g.reserve_serialized(attempt_id=nxt)
  assert s.provider_attempt(nxt) is None
