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


def test_two_audited_failed_predecessors_only(monkeypatch):
    jobs=[{'job_id':'original','state':'failed','attempt':1},{'job_id':'interrupted','state':'failed','attempt':1},{'job_id':'new','state':'done','attempt':1}]
    reader=SimpleNamespace(jobs=lambda tid:jobs,usage=lambda tid:[{'state':'settled'}])
    monkeypatch.setattr(__import__('completion_adapters'),'EvidenceReader',lambda s:reader)
    store=SimpleNamespace(retail_compute_status=lambda tid:{'waived':2},_postgres=SimpleNamespace(transaction=Connection))
    m.check_quiescent_continuation(store,'task','interrupted',original_failed_jobs=['original','interrupted'])
    jobs[2]['state']='failed'
    with pytest.raises(ValueError):m.check_quiescent_continuation(store,'task','interrupted',original_failed_jobs=['original','interrupted'])


def test_unknown_is_allowed_only_for_exact_audited_failed_predecessor():
 u={'state':'failure_waived','attempt_id':'a','job_id':'j','expense_reserved':21394800000}
 proof={'attempt_id':'a','failed_job':'j','provider_expense_actual':None,'conservative_cap_consumption_units':21394800000,'authorization_ref':'fixture','balance_unchanged':True}
 store=SimpleNamespace(audit_entries=lambda tid:[{'action':'item6.provider.unknown_failure_waived','detail':proof}])
 m.require_reconciled_usage(store,'task',[u],{'j'})
 with pytest.raises(ValueError):m.require_reconciled_usage(store,'task',[u],{'different'})
 with pytest.raises(ValueError):m.require_reconciled_usage(store,'task',[u|{'expense_actual':0}],{'j'})
 proof['conservative_cap_consumption_units']=0
 with pytest.raises(ValueError):m.require_reconciled_usage(store,'task',[u],{'j'})


def test_three_failed_predecessors_keep_completed_intent_job(monkeypatch):
 jobs=[{'job_id':i,'state':'failed','attempt':1} for i in ('original','choicefailure','architecturefailure')]+[{'job_id':'accepted-intent','state':'done','attempt':1},{'job_id':'resumed-generator','state':'done','attempt':1}]
 reader=SimpleNamespace(jobs=lambda tid:jobs,usage=lambda tid:[{'state':'settled'}])
 monkeypatch.setattr(__import__('completion_adapters'),'EvidenceReader',lambda s:reader)
 store=SimpleNamespace(retail_compute_status=lambda tid:{'waived':3,'settled':2},_postgres=SimpleNamespace(transaction=Connection))
 m.check_quiescent_continuation(store,'task','architecturefailure',original_failed_jobs=['original','choicefailure','architecturefailure'])
 jobs[-1]['state']='failed'
 with pytest.raises(ValueError):m.check_quiescent_continuation(store,'task','architecturefailure',original_failed_jobs=['original','choicefailure','architecturefailure'])
