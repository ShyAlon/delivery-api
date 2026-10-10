import json,sys
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).parents[1]/'scripts/item6'))
from single_config import create_single,load_env,write_env,review,compile_config
from vendor_config import generate_snapshot,migrate_env
from test_item6_model_data import P,A
from model_data import PRICING_URL


def filled(tmp_path):
    root=tmp_path/'run';create_single(root);d,_=load_env(root)
    d.update(PAYPAL_CLIENT_ID='fixture-id',PAYPAL_CLIENT_SECRET='fixture-secret',DEEPSEEK_API_KEY='fixture-key',SANDBOX_MERCHANT_ID='merchant-fixture',SANDBOX_WEBHOOK_ID='webhook-fixture')
    p=root/'private/.env';p.unlink();write_env(p,d)
    generate_snapshot(root,'deepseek-flash',lambda u:P if u==PRICING_URL else A)
    return root


def test_optional_rates_generated_snapshot_review_and_compile(tmp_path):
    root=filled(tmp_path);r=review(root)
    assert 'fixture-key' not in json.dumps(r)
    review(root,r['review_hash']);result=compile_config(root)
    runtime=Path(result['generated_runtime']);catalog=json.loads((runtime/'catalog.json').read_text())[0]
    assert catalog['retail_input']==12500 and catalog['retail_formula']['price_per_token']=='0.0000125'
    assert catalog['provider_snapshot']['sources'][0]['sha256']
    assert json.loads((runtime/'context.json').read_text())['context_tokens']==128000
    assert json.loads((runtime/'context.json').read_text())['protective_run_ceiling'] is True


def test_manual_fields_removed_candidate_preserves_original_and_secrets(tmp_path):
    root=filled(tmp_path);d,_=load_env(root);d.update(PRICING_BAND='peak',CONTEXT_TOKENS=1000000,RETAIL_INPUT_UNITS_PER_TOKEN=1,RETAIL_OUTPUT_UNITS_PER_TOKEN=1)
    p=root/'private/.env';p.unlink();write_env(p,d);before=p.read_bytes()
    result=migrate_env(root);candidate=Path(result['proposed_env_file']).read_text()
    assert p.read_bytes()==before and 'PRICING_BAND' not in candidate
    assert 'fixture-key' in candidate and 'PRICE_PER_TOKEN' not in candidate


def test_snapshot_change_invalidates_review(tmp_path):
    root=filled(tmp_path);r=review(root);review(root,r['review_hash'])
    snapshot=next((root/'private').glob('vendor-snapshot-*.json'))
    d=json.loads(snapshot.read_text());d['model_version']='changed';snapshot.write_text(json.dumps(d))
    with pytest.raises(ValueError):compile_config(root)


def test_protective_owner_ceiling_is_not_a_vendor_fact_or_paid_admission(tmp_path):
    from delivery_api.admission_catalog import ReviewedContextCeiling
    from delivery_api.attempt_ledger import AdmissionDenied
    root=filled(tmp_path);r=review(root);review(root,r['review_hash']);result=compile_config(root)
    context=json.loads((Path(result['generated_runtime'])/'context.json').read_text())
    with pytest.raises(AdmissionDenied,match='verified complete-request'):
        ReviewedContextCeiling(**context)(stage='needs',serialized_request=b'{"model":"deepseek-flash","max_tokens":1}',max_output_tokens=1)


def test_legacy_duplicate_fields_are_not_silently_ignored(tmp_path):
    root=filled(tmp_path);d,_=load_env(root);d['RETAIL_INPUT_UNITS_PER_TOKEN']=1
    p=root/'private/.env';p.unlink();write_env(p,d)
    with pytest.raises(ValueError,match='migrate-d13'):review(root)


def test_live_source_change_does_not_reprice_or_pass(tmp_path):
    from vendor_config import verify_current_sources
    root=filled(tmp_path);snapshot=json.loads(next((root/'private').glob('vendor-snapshot-*.json')).read_text())
    assert verify_current_sources(snapshot,lambda u:P if u==PRICING_URL else A)
    with pytest.raises(ValueError,match='changed'):
        verify_current_sources(snapshot,lambda u:(P+'changed') if u==PRICING_URL else A)
