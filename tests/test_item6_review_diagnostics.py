import json,sys,subprocess
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).parents[1]/'scripts/item6'))
from review_diagnostics import binding_checks,diagnose_review
from test_item6_vendor_config import filled
from single_config import load_env,write_env,review

@pytest.mark.parametrize('bad',[None,123,'https://example.invalid/sensitive-token','with space','é','', 'x'*128])
def test_binding_check_names_field_not_value(bad):
    checks=binding_checks({'SANDBOX_MERCHANT_ID':bad,'SANDBOX_WEBHOOK_ID':'ok-id'})
    assert checks[0]['ok'] is False and checks[1]['ok'] is True
    assert checks[0]['where']=='private/.env:SANDBOX_MERCHANT_ID'
    if isinstance(bad,str) and len(bad)>3:assert bad not in json.dumps(checks)


def test_review_failure_batches_ids_and_never_shows_credentials(tmp_path):
    root=filled(tmp_path);d,_=load_env(root);d.update(SANDBOX_MERCHANT_ID='bad sensitive merchant',SANDBOX_WEBHOOK_ID=123)
    p=root/'private/.env';p.unlink();write_env(p,d)
    result=diagnose_review(root);assert result['review_ready'] is False
    assert len(result['checks'])==2 and all('SANDBOX_' in c['where'] for c in result['checks'])
    text=json.dumps(result);assert 'fixture-key' not in text and 'sensitive merchant' not in text
    from review_diagnostics import ReviewPrerequisite
    with pytest.raises(ReviewPrerequisite):review(root)
    cmd=[sys.executable,str(Path(__file__).parents[1]/'scripts/item6/scaffold.py'),'review-config',str(root)]
    out=subprocess.run(cmd,capture_output=True,text=True)
    data=json.loads(out.stdout);assert out.returncode==1 and len(data['checks'])==2
    assert 'fixture-secret' not in out.stdout and 'sensitive merchant' not in out.stdout
    assert data['action_completed'] is False


def test_diagnose_ready_is_read_only(tmp_path):
    root=filled(tmp_path);before={p.name:p.read_bytes() for p in (root/'private').iterdir()}
    result=diagnose_review(root);assert result['review_ready'] and not result['files_changed']
    assert before=={p.name:p.read_bytes() for p in (root/'private').iterdir()}


def test_categories_preserve_requirement_but_not_arbitrary_error():
    from review_diagnostics import safe_category
    from delivery_api.payment_errors import PaymentRejected
    assert safe_category(PaymentRejected('bounded PayPal resource ID required'))=='bounded PayPal resource ID required'
    assert 'top-secret' not in safe_category(ValueError('top-secret'))
    assert safe_category(ValueError('price_per_token: rate not representable at 1000000000 units per credit')).startswith('price_per_token: rate not representable')


def test_snapshot_failure_names_category(tmp_path):
    root=filled(tmp_path)
    snapshot=next((root/'private').glob('vendor-snapshot-*.json'))
    snapshot.unlink()
    result=diagnose_review(root)
    assert result['review_ready'] is False
    assert result['checks'][0]['checked']=='generated vendor snapshot'
    assert result['checks'][0]['found'].startswith('one generated current vendor snapshot required')
