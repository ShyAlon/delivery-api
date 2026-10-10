#!/usr/bin/env python3
"""Fresh private run workspace and explicit placeholders. Never starts a run."""
import argparse,datetime,json,os,re,uuid
from pathlib import Path
from isolation import RunIsolation,parse_loopback_service,safe_model_route

PLACEHOLDER='REPLACE_LOCALLY'


def scaffold(destination):
    destination=Path(destination).absolute()
    if destination.exists() or destination.is_symlink() or any(p.is_symlink() for p in destination.parents):
        raise ValueError('new non-symlink run directory required; never reuse or overwrite')
    run_id=uuid.uuid4().hex;db='requi_item6_'+datetime.date.today().strftime('%Y%m%d')+'_'+run_id[:12]
    destination.mkdir(mode=0o700);private=destination/'private';private.mkdir(mode=0o700);(destination/'evidence').mkdir(mode=0o700)
    def write(name,body):RunIsolation.write(private/name,body)
    write('credentials.json',{'client_id':PLACEHOLDER,'client_secret':PLACEHOLDER,'deepseek_api_key':PLACEHOLDER})
    write('catalog.json',[{'provider':'deepseek','model':PLACEHOLDER,'price_version':PLACEHOLDER,'source':PLACEHOLDER,
        'expense_input':None,'expense_output':None,'expense_cache_hit':None,'expense_cache_miss':None,
        'retail_input':None,'retail_output':None,'expense_units_per_usd':None,'retail_units_per_credit':1000,
        'usage_categories':['input_tokens','output_tokens','cache_hit_tokens','cache_miss_tokens']}])
    write('context.json',{'model':PLACEHOLDER,'context_tokens':None,'maximum_output_tokens':None,'source':PLACEHOLDER,'bound_version':PLACEHOLDER})
    write('run.json',{'root':'/evidence/run','database_name':db,'database_host':'127.0.0.1','service_file':'/private/services.conf',
        'admin_service':'item6-admin','tenant_service':'item6-tenant','payment_service':'item6-payment','model':PLACEHOLDER,
        'catalog_file':'/private/catalog.json','context_file':'/private/context.json','paypal_secret_file':'/private/credentials.json',
        'verified_sandbox_merchant':PLACEHOLDER,'sandbox_webhook_id':PLACEHOLDER,'operator_registry_version':'item6-local-'+run_id[:12]})
    fd=os.open(private/'services.conf',os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
    with os.fdopen(fd,'w') as f:
        for label,user in [('admin','requi'),('tenant','delivery_app'),('payment','delivery_payment_test')]:
            f.write(f'[item6-{label}]\nhost=127.0.0.1\nport=55439\ndbname={db}\nuser={user}\n\n')
    write('webhook.example.json',{'event':{'NOTICE':'Authentic captured sandbox event only, not simulator data'},'headers':{'NOTICE':'Original transmission headers required; verified by PayPal before use'}})
    RunIsolation.write(destination/'run-id.json',{'run_id':run_id,'database_name':db,'run_started':False,'launch_authorized':False})
    # The launcher has no go default and quotes local paths correctly.
    launcher='''#!/usr/bin/env bash
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
if [ "$#" -gt 0 ]; then
  [ "$#" -eq 2 ] && [ "$1" = --go ] && [ "$2" = RUN_ID ] || { echo 'Only the exact separately approved --go RUN_ID is accepted'; exit 2; }
fi
docker run --name "requi-item6-RUN_ID" -it --read-only --cap-drop ALL --security-opt no-new-privileges \
  --tmpfs /tmp:rw,nosuid,nodev,size=2g \
  --mount "type=bind,src=$HERE/private,dst=/private,readonly" \
  --mount "type=bind,src=$HERE/evidence,dst=/evidence" \
  requi-item6-test --config /private/run.json --run-id RUN_ID "$@"
'''.replace('RUN_ID',run_id)
    fd=os.open(destination/'run.sh',os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o700)
    with os.fdopen(fd,'w') as f:f.write(launcher)
    (destination/'NEXT.md').write_text('Fill the private placeholders locally. Keys never belong in chat. Verify sandbox app merchant/webhook IDs and current model price/context sources. Run scaffold.py validate before Docker dry preflight. The dry run starts nothing. Preserve the named container and evidence; remove only after review before a separately approved fresh run. A real run still requires Shy\'s explicit go. No code creates that approval.\n')
    (destination/'NEXT.md').chmod(0o600)
    return {'run_id':run_id,'database_name':db,'scaffolded':True,'run_started':False,'launch_authorized':False}


def validate(destination):
    destination=Path(destination).absolute();missing=[];data={}
    if destination.is_symlink() or destination.stat().st_mode&0o077:raise ValueError('private run directory required')
    for name in ('private','evidence'):
        p=destination/name
        if p.is_symlink() or not p.is_dir() or p.stat().st_mode&0o077:raise ValueError('private child directories required')
    def unresolved(value):
        if value is None:return True
        if isinstance(value,str):return not value.strip() or 'REPLACE' in value or value=='PLACEHOLDER'
        if isinstance(value,dict):return any(unresolved(v) for v in value.values())
        if isinstance(value,list):return any(unresolved(v) for v in value)
        return False
    for name in ('run.json','catalog.json','context.json','credentials.json','services.conf'):
        p=destination/'private'/name
        if p.is_symlink() or not p.is_file() or p.stat().st_mode&0o077 or p.stat().st_size>1024*1024:raise ValueError('private bounded template files required')
        if name.endswith('.json'):
            data[name]=json.loads(p.read_text())
            if unresolved(data[name]):missing.append(name)
    if missing:return {'ready_for_dry_preflight':False,'missing_files':missing,'run_started':False}
    run=data['run.json'];prices=data['catalog.json'];context=data['context.json'];secrets=data['credentials.json']
    required={'root','database_name','database_host','service_file','admin_service','tenant_service','payment_service','model','catalog_file','context_file','paypal_secret_file','verified_sandbox_merchant','sandbox_webhook_id','operator_registry_version'}
    if set(run)!=required:raise ValueError('exact run configuration required')
    for field,value in {'service_file':'/private/services.conf','catalog_file':'/private/catalog.json','context_file':'/private/context.json','paypal_secret_file':'/private/credentials.json','admin_service':'item6-admin','tenant_service':'item6-tenant','payment_service':'item6-payment'}.items():
        if run[field]!=value:raise ValueError('scaffold paths and service labels must stay fixed')
    metadata=destination/'run-id.json'
    if metadata.is_symlink() or metadata.stat().st_mode&0o077:raise ValueError('private run identity required')
    identity=json.loads(metadata.read_text())
    if identity['database_name']!=run['database_name'] or identity['run_started'] is not False or identity['launch_authorized'] is not False:raise ValueError('fresh scaffold identity required')
    # Validate identifier syntax only. Host paths and mounts are checked in Docker.
    if not re.fullmatch('[a-f0-9]{32}',identity['run_id']):raise ValueError('exact fresh run id required')
    if any((destination/'evidence').iterdir()):raise ValueError('empty new evidence directory required')
    safe_model_route('deepseek',run['model'],True)
    if not prices or any(row.get('provider')!='deepseek' or row.get('model')!=run['model'] or row.get('retail_units_per_credit')!=1000 for row in prices):raise ValueError('only the exact DeepSeek model and 1000-unit scale required')
    if set(secrets)!={'client_id','client_secret','deepseek_api_key'}:raise ValueError('exact local credential fields required')
    if run['root']!='/evidence/run' or run['database_host']!='127.0.0.1':raise ValueError('scaffold destination changed')
    for name,expected_user in [('admin_service','requi'),('tenant_service','delivery_app'),('payment_service','delivery_payment_test')]:
        result=parse_loopback_service((destination/'private/services.conf').read_text(),run[name])
        if result!={'host':'127.0.0.1','database_name':run['database_name'],'port':55439}:raise ValueError('exact isolated service destination required')
        import configparser
        cfg=configparser.ConfigParser();cfg.read(destination/'private/services.conf')
        if cfg[run[name]]['user']!=expected_user:raise ValueError('exact role separation required')
    from delivery_api.admission_catalog import catalog_from_environment,ReviewedContextCeiling
    from delivery_api.launch_admission_policy import validate_launch_quote
    from delivery_api.paypal_adapter import PayPalBinding
    PayPalBinding(run['verified_sandbox_merchant'],run['sandbox_webhook_id'],False)
    ceiling=ReviewedContextCeiling(**context)
    if ceiling.model!=run['model']:raise ValueError('selected model must match reviewed context')
    catalog=catalog_from_environment({'VMODEL_REVIEWED_MODEL_PRICES':json.dumps(prices)},bounders={('serialized','deepseek',run['model']):ceiling})
    quote,_=catalog.resolve_serialized(stage='dry',provider='deepseek',model=run['model'],serialized_request=json.dumps({'model':run['model'],'max_tokens':1}).encode(),max_output_tokens=1)
    validate_launch_quote(quote)
    if quote['retail_units_per_credit']!=1000:raise ValueError('1000-unit account scale required')
    return {'ready_for_dry_preflight':True,'run_started':False,'credentials_reported':False,'bindings_verified_live':False,'launch_authorized':False}

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('action',choices=('create','validate'));p.add_argument('directory',type=Path);a=p.parse_args()
    try:print(json.dumps(scaffold(a.directory) if a.action=='create' else validate(a.directory)))
    except Exception:raise SystemExit('Scaffold validation failed. Check private templates locally; no run started.') from None
