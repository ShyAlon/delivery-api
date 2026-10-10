import importlib.util,json
from dataclasses import replace
from pathlib import Path
import pytest
ROOT=Path(__file__).parents[1]
spec=importlib.util.spec_from_file_location('item6_isolation',ROOT/'scripts/item6/isolation.py');m=importlib.util.module_from_spec(spec)
import sys
sys.modules[spec.name]=m;spec.loader.exec_module(m)


def config(tmp_path):return m.RunIsolation(tmp_path/'fresh','requi_item6_20261010_fixture','127.0.0.1','a'*32)


def test_new_private_scope_has_no_launch_authority_and_never_overwrites(tmp_path):
    cfg=config(tmp_path);root=cfg.initialize();saved=json.loads((root/'run-scope.json').read_text())
    assert not saved['launch_authorized'] and not saved['production_routes_enabled']
    assert root.stat().st_mode&0o777==0o700 and (root/'run-scope.json').stat().st_mode&0o777==0o600
    with pytest.raises(ValueError):cfg.initialize()


@pytest.mark.parametrize('changes',[{'database_name':'production'},{'database_host':'db.example.com'},
    {'paypal_origin':'https://api-m.paypal.com'},{'launch_authorized':True},{'mode':'live'},
    {'run_id':'../../other'},{'root':Path('/')},{'root':Path.home()}])
def test_rejects_production_destinations_or_scope_before_effects(tmp_path,changes):
    cfg=replace(config(tmp_path),**changes)
    with pytest.raises(ValueError):cfg.initialize()
    assert not (tmp_path/'fresh').exists()


def test_symlink_parent_rejected_without_destination_creation(tmp_path):
    actual=tmp_path/'actual';actual.mkdir();link=tmp_path/'link';link.symlink_to(actual,target_is_directory=True)
    with pytest.raises(ValueError):replace(config(tmp_path),root=link/'fresh').initialize()
    assert not (actual/'fresh').exists()


@pytest.mark.parametrize('tail',['host=db.example.com','dbname=production','hostaddr=8.8.8.8','service=live','passfile=/private','port=no'])
def test_pg_service_indirection_or_live_destinations_fail_closed(tail):
    text='[item6]\nhost=127.0.0.1\ndbname=requi_item6_20261010_fixture\npassword=must-not-print\n'+tail+'\n'
    with pytest.raises((ValueError,__import__('configparser').Error)):m.parse_loopback_service(text,'item6')


def test_explicit_route_catalog_and_no_credential_return():
    text='[item6]\nhost=127.0.0.1\ndbname=requi_item6_20261010_fixture\npassword=must-not-print\n'
    assert 'must-not-print' not in str(m.parse_loopback_service(text,'item6'))
    assert m.safe_model_route('deepseek','deepseek-flash',True)['provider']=='deepseek'
    with pytest.raises(ValueError):m.safe_model_route('deepseek','deepseek-flash',False)
    with pytest.raises(ValueError):m.safe_model_route('openai','anything',True)
