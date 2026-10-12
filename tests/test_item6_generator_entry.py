import sys,json,hashlib
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).parents[1]/'scripts/item6'))
import recovery_generator_entry as m


def fixture(tmp_path,monkeypatch):
 dump=tmp_path/'dump';dump.write_bytes(b'fixture')
 identity=tmp_path/'identity';identity.write_text(json.dumps({'subject':'045bafdf-e35b-4b4b-93f2-0cd981b52806','session_id':'16caab89-5c55-4c36-af25-ba65105ed74f','run_id':'4eda39c0684945ff849b582c60dd91f8','signature_verified':True,'source_mode':'mac_acceptance'}))
 topup=tmp_path/'topup';topup.write_text(json.dumps({'kind':'item6-sandbox-topup','offer_id':'credits-5','amount_minor':500,'currency':'USD','credit_units':5*10**9,'sandbox':True,'signature_verified':True,'balance_before':362835621,'balance_after':5362835621,'capture_id':'fixture-capture','event_id':'fixture-event'}))
 source=tmp_path/'source';source.mkdir();(source/'checkpoint').write_bytes(b'fixture')
 runtime=tmp_path/'runtime';runtime.mkdir();(runtime/'catalog').write_bytes(b'fixture')
 monkeypatch.setattr(m,'generator_review',lambda *a:{'checkpoint_id':'1f1c584c-4052-641a-8003-1a6caeab0d48','architecture_partials_present':False})
 return dump,identity,source,runtime,topup


def test_paidreview_binding_and_changed_pack_rejected(tmp_path,monkeypatch):
 args=fixture(tmp_path,monkeypatch);review,sha=m.prepare(*args)
 assert review['architecture_stage_regeneration'] and not review['upstream_replayed'] and not review['new_purchase']
 assert review['maximum_expense_units']==10**12 and review['remaining_original_provider_cap_units']==958338910000
 args[0].write_bytes(b'changed');assert m.prepare(*args)[1]!=sha
 data=json.loads(args[-1].read_text());data['amount_minor']=1150;args[-1].write_text(json.dumps(data))
 with pytest.raises(ValueError):m.prepare(*args)


def test_missing_or_changed_go_cannot_initialize_database(tmp_path,monkeypatch):
 args=fixture(tmp_path,monkeypatch);names=('dump','identity','source','runtime','topup')
 argv=['entry']+sum((['--'+key,str(value)] for key,value in zip(names,args)),[])+['--output',str(tmp_path/'output')]
 calls=[];monkeypatch.setattr(m.subprocess,'run',lambda *a,**kw:calls.append(a))
 monkeypatch.setattr(sys,'argv',argv);m.main();assert calls==[] and not(tmp_path/'output').exists()
 monkeypatch.setattr(sys,'argv',argv+['--go','wrong'])
 with pytest.raises(ValueError):m.main()
 assert calls==[] and not(tmp_path/'output').exists()


def test_phase_failure_review_is_separate_and_binds_known_failure(tmp_path,monkeypatch):
 args=fixture(tmp_path,monkeypatch)
 old,old_sha=m.prepare(*args)
 review,sha=m.prepare(*args,phase_failure_recovery=True)
 assert sha!=old_sha and review['version']==4
 assert review['original_failed_job']=='22535ad84a5ddccf380a88bdddf23e50'
 assert review['phase_failure_recovery'] is True
 assert review['phase_failure_archive_sha256']=='0be3e545e0669b85c3963dc40c53df6a809eab7a5295616c47058d2646873428'
 assert review['maximum_expense_units']==old['maximum_expense_units']
 assert review['remaining_original_provider_cap_units']==old['remaining_original_provider_cap_units']
