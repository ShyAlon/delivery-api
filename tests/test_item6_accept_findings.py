"""Accept-all route for the findings gate: one exact, hash-bound reject per open finding, on a typed phrase."""
import json,sys
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts/item6'))
import accept_findings as A

S=Path(__file__).resolve().parents[1]/'scripts/item6'


def ledger_dict():
    def f(i,state='OPEN'):return {'finding_id':'F-%016x'%i,'rule_id':'review.llm','state':state,'artifact_revision':'r%d'%i,'detail':{'scope':'test'},'targets':['T-%d'%i]}
    return {'version':1,'findings':{'a':f(2),'b':f(1),'c':f(3,'REJECTED')},'incomplete':[{'scope':'review:round-1-chunk-2','reason':'chunk returned 34 findings (bound 32)','returned':34}],'history':[]}


def test_script_has_one_exact_entry_per_open_finding_sorted_and_hash_bound():
    s=A.build_script(ledger_dict())
    assert s['version']==1 and s['authored_by']=='founder'
    ids=[e['selector']['finding_id'] for e in s['dispositions']]
    assert ids==['F-0000000000000001','F-0000000000000002']
    for e in s['dispositions']:
        assert set(e)=={'selector','action','expected_artifact_sha256','rationale','reason_subtype'} and set(e['selector'])=={'finding_id'}
        assert e['action']=='reject' and e['reason_subtype']=='accepted_risk' and e['rationale']==A.RATIONALE


def test_phrase_names_the_count_and_the_script_hash_and_changes_with_the_ledger():
    s=A.build_script(ledger_dict());p=A.phrase(s)
    assert p=='ACCEPT 2 FINDINGS '+A.script_sha(s)[:8]
    other=ledger_dict();other['findings']['a']['artifact_revision']='changed'
    assert A.phrase(A.build_script(other))!=p
    assert A.build_script(ledger_dict())==s


def test_summary_counts_by_rule_and_scope_and_mentions_incomplete_checks():
    text='\n'.join(A.summary_lines(ledger_dict(),A.build_script(ledger_dict())))
    assert '2 open finding(s)' in text and 'review.llm / test' in text and '1 incomplete review check' in text


def test_the_library_ledger_accepts_the_script_and_the_gate_opens():
    pytest.importorskip('spec_generator.findings_ledger')
    from spec_generator.findings_ledger import Ledger,SEMANTIC,revision_of
    led=Ledger()
    for i in range(3):
        led.add(rule_id='review.llm',rule_version='review-round-1',source='review:round-1',kind=SEMANTIC,targets=['T-%d'%i],artifact_revision=revision_of('body%d'%i),evidence=[],summary='s%d'%i,detail={'scope':'test'})
    led.mark_incomplete('review:round-1-chunk-1','chunk returned 34 findings (bound 32)',returned=34) if hasattr(led,'mark_incomplete') else None
    d=led.to_dict();assert Ledger.from_dict(d).gate({})['blocked']
    script=A.build_script(d);assert len(script['dispositions'])==3
    out=A.apply_to_ledger(d,script,{})
    gate=Ledger.from_dict(out).gate({})
    assert gate['blocked'] is False
    states={f['state'] for f in out['findings'].values()};assert states=={'REJECTED'}
    assert all(f['closure']['subtype']=='accepted_risk' and f['closure']['rationale']==A.RATIONALE for f in out['findings'].values())


def test_a_changed_artifact_makes_the_script_refuse_instead_of_accepting_silently():
    pytest.importorskip('spec_generator.findings_ledger')
    from spec_generator.findings_ledger import Ledger,SEMANTIC,LedgerError,revision_of
    led=Ledger();led.add(rule_id='review.llm',rule_version='v',source='review:round-1',kind=SEMANTIC,targets=['T-1'],artifact_revision=revision_of('b'),evidence=[],summary='s',detail={})
    d=led.to_dict();script=A.build_script(d);script['dispositions'][0]['expected_artifact_sha256']='stale'
    with pytest.raises(LedgerError):A.apply_to_ledger(d,script,{})


def test_driver_executor_and_checkpoint_reader_are_wired_for_resume_at_assemble():
    r=(S/'resume_entry.py').read_text();x=(S/'pipeline_executor.py').read_text();g=(S/'recovery_generator.py').read_text()
    assert "'review_complete_present':True" in g and "not review_part.is_file()" in g and '**complete_extra}' in g
    assert "expected_next=('assemble',) if generator_resume.get('review_complete_present')" in x
    assert "graph.update_state(cfg,{'findings_ledger':ledger},as_node='review')" in x and 'accept_script=None' in x
    assert "typed.strip()!=want" in r and 'ledger_sha256' in r and 'accept_script=accept' in r
    assert 'findings.record(' in r and 'nothing was changed' in r
    assert r.index('typed.strip()!=want')<r.index('execute_continuation(store=store')
