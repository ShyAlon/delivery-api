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
