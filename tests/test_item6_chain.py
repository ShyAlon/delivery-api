import importlib.util,sys
from pathlib import Path
import pytest
from test_item6_isolation import m,config
ROOT=Path(__file__).parents[1]
spec=importlib.util.spec_from_file_location('item6_chain',ROOT/'scripts/item6/chain.py');chain=importlib.util.module_from_spec(spec);spec.loader.exec_module(chain)
A='00000000-0000-0000-0000-000000000001'


def adapters(c):
    payloads={
        'identity':dict(subject=A,session_id=A,signature_verified=True,active_session=True,fresh_signup=True),
        'operator':dict(subject=A,registry_version='fixture-v1',explicit_grant=True,capabilities=['payments.read']),
        'sandbox_purchase':dict(subject=A,purchase_id='fixture-purchase',payment_id='fixture-capture',merchant_id='fixture-merchant',sandbox=True,amount_minor=500,credit_units=5000,posted_count=1,signature_verified=True,balance_units=5000),
        'pipeline':dict(subject=A,task_id='fixture-task',session_id='fixture-engine',model_provider='deepseek',model='fixture-no-call',job_ids=['fixture-job'],canonical_validated=True,artifact_sha256={'spec/srs.json':'a'*64},all_stages_complete=True,usage_settled=True),
        'gate_edit':dict(task_id='fixture-task',owner_id=A,source_review_sha256='a'*64,fresh_review_sha256='b'*64,stale_rejected=True,same_phase_reapproval=True,source_preserved=True,installed_lineage_verified=True),
        'final_artifacts':dict(task_id='fixture-task',owner_id=A,session_id='fixture-engine',files_sha256={'spec/srs.json':'b'*64},download_verified=True,canonical_validated=True),
        'recovery':dict(task_id='fixture-task',owner_id=A,quiescent=True,unknown_attempts=0,blind_replays=0,foreign_owner_denied=True)}
    return {s:(lambda scope,previous,s=s,p=p:{'stage':s,'run_id':scope.run_id,'source_mode':scope.mode,'production_effect':False,**p}) for s,p in payloads.items()}


def test_connected_hermetic_scope_receipts_and_progress_never_grant_launch(tmp_path):
    c=config(tmp_path);lines=[];h=chain.AcceptanceChain(c,adapters=adapters(c),progress=lines.append);result=h.execute()
    assert result['result']=='HERMETIC_WIRING_PASS' and not result['launch_authorized']
    assert len(result['evidence_sha256'])==7 and len(lines)==15
    assert all('T' in line and '| ' in line for line in lines)
    with pytest.raises(ValueError):h.execute()


@pytest.mark.parametrize('stage,changes',[('sandbox_purchase',{'sandbox':False}),('sandbox_purchase',{'posted_count':2}),
 ('identity',{'signature_verified':False}),('operator',{'explicit_grant':False}),('pipeline',{'all_stages_complete':False}),
 ('pipeline',{'usage_settled':False}),('gate_edit',{'fresh_review_sha256':'a'*64}),('gate_edit',{'stale_rejected':False}),
 ('final_artifacts',{'download_verified':False}),('recovery',{'unknown_attempts':1}),('recovery',{'foreign_owner_denied':False}),
 ('pipeline',{'subject':'00000000-0000-0000-0000-000000000002'}),('identity',{'source_mode':'mac_acceptance'}),('identity',{'token':'never serialize'})])
def test_failure_preserves_prior_evidence_stops_later_calls_no_credential_dump(tmp_path,stage,changes):
    c=config(tmp_path);a=adapters(c);old=a[stage];a[stage]=lambda scope,previous:old(scope,previous)|changes
    with pytest.raises(RuntimeError,match='incomplete'):chain.AcceptanceChain(c,adapters=a).execute()
    assert (c.root/'blocked.json').exists() and not (c.root/'result.json').exists()
    assert not (c.root/(stage+'.json')).exists()
    assert 'never serialize' not in (c.root/'blocked.json').read_text()


def test_mac_adapter_chain_cannot_start_without_exact_run_go(tmp_path):
    from dataclasses import replace
    c=replace(config(tmp_path),mode='mac_acceptance');a=adapters(c)
    with pytest.raises(ValueError,match='go'):chain.AcceptanceChain(c,adapters=a).execute()
    assert not c.root.exists()
