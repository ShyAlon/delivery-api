import sys
from pathlib import Path
from types import SimpleNamespace
import pytest
sys.path.insert(0,str(Path(__file__).parents[1]/'scripts/item6'))
import recovery_continuation as m

class Connection:
    def __enter__(self):return self
    def __exit__(self,*a):pass
    def execute(self,*a):return SimpleNamespace(fetchall=lambda:[{'installation_id':'fixture'}])


def test_only_original_failed_job_exempt_new_failure_blocks(monkeypatch):
    jobs=[{'job_id':'old','state':'failed','attempt':1},{'job_id':'new','state':'done','attempt':1}]
    usage=[{'state':'settled','input_estimate_overrun':False}]
    reader=SimpleNamespace(jobs=lambda tid:jobs,usage=lambda tid:usage)
    monkeypatch.setattr(__import__('completion_adapters'),'EvidenceReader',lambda s:reader)
    store=SimpleNamespace(retail_compute_status=lambda tid:{'waived':1,'settled':2},_postgres=SimpleNamespace(transaction=Connection))
    p=m.check_quiescent_continuation(store,'task','old')
    assert p['uninterrupted_acceptance'] is False and p['unknown_attempts']==0
    jobs[1]['state']='failed'
    with pytest.raises(ValueError):m.check_quiescent_continuation(store,'task','old')
    jobs[1]['state']='done';usage[0]['state']='uncertain'
    with pytest.raises(ValueError):m.check_quiescent_continuation(store,'task','old')
