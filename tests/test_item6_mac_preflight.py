"""Dry CLI performs no DB/network effects and requires exact go before connection."""
import json,os,subprocess,sys
from pathlib import Path
from test_item6_isolation import config


def test_mac_config_dry_default_and_wrong_go_never_connects(tmp_path):
 root=Path(__file__).parents[1]
 def write(name,body):
  p=tmp_path/name;p.write_text(json.dumps(body));p.chmod(0o600);return str(p)
 service=tmp_path/'services';service.write_text('\n'.join('[%s]\nhost=127.0.0.1\nport=9\ndbname=requi_item6_20261010_dry\nuser=fixture\n'%x for x in ('admin','tenant','payment')));service.chmod(0o600)
 prices=write('catalog.json',[{'provider':'deepseek','model':'fixture','price_version':'hermetic','source':'fixture',
  'expense_input':1,'expense_output':2,'expense_cache_hit':1,'expense_cache_miss':1,'retail_input':1,'retail_output':2,
  'retail_units_per_credit':10**9,'expense_units_per_usd':1000000,'usage_categories':['input_tokens','output_tokens','cache_hit_tokens','cache_miss_tokens']}])
 context=write('context.json',{'model':'fixture','context_tokens':100000,'maximum_output_tokens':10000,'source':'fixture','bound_version':'hermetic'})
 secrets=write('secret.json',{'client_id':'fixture','client_secret':'fixture','deepseek_api_key':'not-a-key'})
 cfg=write('run.json',{'root':str(tmp_path/'fresh'),'database_name':'requi_item6_20261010_dry','database_host':'127.0.0.1',
  'service_file':str(service),'admin_service':'admin','tenant_service':'tenant','payment_service':'payment','model':'fixture',
  'catalog_file':prices,'context_file':context,'paypal_secret_file':secrets,'verified_sandbox_merchant':'fixture',
  'sandbox_webhook_id':'fixture','operator_registry_version':'hermetic'})
 cmd=[sys.executable,str(root/'scripts/item6/mac_run.py'),'--config',cfg,'--run-id','a'*32]
 result=subprocess.run(cmd,capture_output=True,text=True)
 assert result.returncode==0,result.stderr
 assert json.loads(result.stdout)['network_started'] is False
 assert not (tmp_path/'fresh').exists()
 wrong=subprocess.run(cmd+['--go','b'*32],capture_output=True,text=True)
 assert wrong.returncode==1 and 'fixture' not in wrong.stderr and not (tmp_path/'fresh').exists()
