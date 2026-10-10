"""Static workflow contract: manual sources, least privilege, no execution rail."""
from pathlib import Path
import yaml

ROOT=Path(__file__).resolve().parents[1]


def test_manual_exact_four_sources_and_no_dispatch_side_effects():
    # BaseLoader keeps YAML 1.1 from turning the GitHub 'on' key into True.
    workflow=yaml.load((ROOT/'.github/workflows/item6-package.yml').read_text(),Loader=yaml.BaseLoader)
    assert set(workflow['on'])=={'workflow_dispatch'}
    assert set(workflow['on']['workflow_dispatch']['inputs'])=={'delivery_sha','engine_sha','generator_sha','contracts_sha'}
    assert workflow['permissions']=={'contents':'read'}
    assert workflow['jobs']['package']['timeout-minutes']=='10'
    assert workflow['concurrency']['cancel-in-progress']=='false'
    steps=workflow['jobs']['package']['steps']
    checkouts=[s for s in steps if s.get('uses','').startswith('actions/checkout@')]
    assert len(checkouts)==4 and all(s['with']['persist-credentials']=='false' for s in checkouts)
    assert {s['with'].get('repository','ShyAlon/delivery-api') for s in checkouts}=={'ShyAlon/delivery-api','ShyAlon/interview-engine','ShyAlon/spec-generator','ShyAlon/vmodel-contracts'}
    raw=(ROOT/'.github/workflows/item6-package.yml').read_text()
    for forbidden in ('docker ','--go','paypal','DEEPSEEK_API_KEY','packages: write','gh workflow run'):
        assert forbidden not in raw
    assert 'Source checkout SHA mismatch' in raw and 'z.testzip()' in raw
    uploads=[s for s in steps if s.get('uses','').startswith('actions/upload-artifact@')]
    assert len(uploads)==1 and uploads[0]['with']['retention-days']=='7'
    assert uploads[0]['with']['path']=='requi-item6-package.zip'


def test_package_scripts_follow_selected_delivery_source_not_invocation_location():
    text=(ROOT/'scripts/item6/prepare-package.py').read_text()
    assert "here=a.delivery.resolve()/'scripts/item6'" in text
    assert 'tracked source changes must be committed' in text
    assert 'uncommitted package input' in text
