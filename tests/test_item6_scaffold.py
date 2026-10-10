"""No subprocesses, live keys, Docker or provider/payment transport."""
import importlib.util,json,os,sys
from pathlib import Path
import pytest

SCRIPTS=Path(__file__).resolve().parents[1]/'scripts/item6'
sys.path.insert(0,str(SCRIPTS))
from scaffold import scaffold,validate


def fill(root):
    private=root/'private'
    def update(name,values):
        p=private/name;d=json.loads(p.read_text());d.update(values);p.write_text(json.dumps(d))
    update('run.json',{'model':'fixture-model','verified_sandbox_merchant':'fixture-merchant','sandbox_webhook_id':'fixture-webhook'})
    update('credentials.json',{'client_id':'fixture-id','client_secret':'fixture-secret','deepseek_api_key':'fixture-key'})
    update('context.json',{'model':'fixture-model','context_tokens':1000,'maximum_output_tokens':100,'source':'hermetic fixture','bound_version':'fixture-v1'})
    (private/'catalog.json').write_text(json.dumps([{'provider':'deepseek','model':'fixture-model','price_version':'fixture-v1','source':'hermetic fixture','expense_input':1,'expense_output':2,'retail_input':3,'retail_output':4,'expense_cache_hit':1,'expense_cache_miss':1,'expense_units_per_usd':1000000,'retail_units_per_credit':1000,'usage_categories':['input_tokens','output_tokens','cache_hit_tokens','cache_miss_tokens']}]))


def test_private_fresh_templates_and_dry_launcher(tmp_path):
    root=tmp_path/'new run';result=scaffold(root)
    assert result['run_started'] is False and result['launch_authorized'] is False
    for p in (root,root/'private',root/'evidence'):assert p.stat().st_mode&0o777==0o700
    for p in (root/'private').iterdir():assert p.stat().st_mode&0o777==0o600
    launcher=(root/'run.sh').read_text()
    assert '--name' in launcher and '--rm' not in launcher and '--go' in launcher
    assert '"$@"' in launcher and '"type=bind,src=$HERE/private,dst=/private,readonly"' in launcher
    assert validate(root)=={'ready_for_dry_preflight':False,'missing_files':['run.json','catalog.json','context.json','credentials.json'],'run_started':False}
    assert (root/'evidence').iterdir() and not list((root/'evidence').iterdir())


def test_structurally_filled_fixture_is_not_live_verification(tmp_path):
    root=tmp_path/'run';scaffold(root);fill(root);r=validate(root)
    assert r['ready_for_dry_preflight'] is True
    assert r['bindings_verified_live'] is False and r['run_started'] is False and r['launch_authorized'] is False
    assert 'fixture-secret' not in json.dumps(r)


@pytest.mark.parametrize('mutation',['path','db','scale','provider','model','service','permission','symlink','evidence'])
def test_fail_closed(tmp_path,mutation):
    root=tmp_path/'run';scaffold(root);fill(root)
    p=root/'private/run.json';d=json.loads(p.read_text())
    if mutation=='path':d['catalog_file']='/other/catalog.json';p.write_text(json.dumps(d))
    elif mutation=='db':d['database_name']='production';p.write_text(json.dumps(d))
    elif mutation in ('scale','provider','model'):
        p=root/'private/catalog.json';d=json.loads(p.read_text());d[0][{'scale':'retail_units_per_credit','provider':'provider','model':'model'}[mutation]]={'scale':1,'provider':'other','model':'other'}[mutation];p.write_text(json.dumps(d))
    elif mutation=='service':
        p=root/'private/services.conf';p.write_text(p.read_text().replace('user=delivery_payment_test','user=requi'))
    elif mutation=='permission':p.chmod(0o644)
    elif mutation=='symlink':
        original=root/'private/run.json';target=root/'copy';target.write_text(original.read_text());target.chmod(0o600);original.unlink();original.symlink_to(target)
    else:(root/'evidence/existing').write_text('do not replay')
    with pytest.raises(Exception):validate(root)


def test_no_reuse_overwrite_or_symlink_parent(tmp_path):
    root=tmp_path/'run';scaffold(root)
    with pytest.raises(ValueError):scaffold(root)
    link=tmp_path/'link';link.symlink_to(root,target_is_directory=True)
    with pytest.raises(ValueError):scaffold(link/'other')
