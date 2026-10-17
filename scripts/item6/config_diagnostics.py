"""Read-only founder diagnostics. Never serialize arbitrary config values/keys."""
from pathlib import Path
import json


def diagnose(root):
    root=Path(root).absolute();checks=[];objects={}
    def record(where,checked,expected,found,ok):
        checks.append({'where':where,'checked':checked,'expected':expected,'found':found,'ok':ok})
    def file(name,kind):
        p=root/name;where=name
        if any(q.is_symlink() for q in (p,*p.parents)):record(where,'file safety','regular non-symlink '+kind,'symlink',False);return
        if not p.exists():record(where,'presence',kind,'missing',False);return
        mode=p.stat().st_mode&0o777
        if mode&0o077:record(where,'permissions','owner-only (600 files, 700 directories)',oct(mode),False)
        if kind=='directory':
            if not p.is_dir():record(where,'type','directory','not a directory',False);return
            if name=='evidence' and any(p.iterdir()):record(where,'fresh run','empty evidence directory','contains existing entries; preserve them',False)
            return
        if not p.is_file() or p.stat().st_size>1024*1024:record(where,'bounded type','regular file <= 1 MiB','wrong type or too large',False);return
        if kind=='JSON':
            try:objects[name]=json.loads(p.read_text())
            except json.JSONDecodeError as e:record(where,'JSON syntax','valid JSON',f'invalid JSON at line {e.lineno}, column {e.colno}; contents suppressed',False)
            except Exception:record(where,'JSON read','readable UTF-8 JSON','unreadable or invalid encoding',False)
    if any(p.is_symlink() for p in (root,*root.parents)):record('.','path safety','no symlink ancestors','symlink in path',False)
    if not root.is_dir():record('.','run directory','existing scaffold directory','missing or not directory',False)
    else:
        if root.stat().st_mode&0o077:record('.','permissions','owner-only directory','insecure mode '+oct(root.stat().st_mode&0o777),False)
        for name,kind in [('private','directory'),('evidence','directory'),('run-id.json','JSON'),('private/run.json','JSON'),('private/credentials.json','JSON'),('private/catalog.json','JSON'),('private/context.json','JSON'),('private/services.conf','text')]:file(name,kind)
        target=root/'private/.env'
        if target.exists() or target.is_symlink():record('private/.env','one-time import destination','absent; never overwrite existing .env','already exists; use/review existing source',False)
    schemas={
        'run-id.json':{'run_id','database_name','run_started','launch_authorized'},
        'private/run.json':{'root','database_name','database_host','service_file','admin_service','tenant_service','payment_service','model','catalog_file','context_file','paypal_secret_file','verified_sandbox_merchant','sandbox_webhook_id','operator_registry_version'},
        'private/credentials.json':{'client_id','client_secret','deepseek_api_key'},
        'private/context.json':{'model','context_tokens','maximum_output_tokens','source','bound_version'}}
    for name,keys in schemas.items():
        obj=objects.get(name)
        if obj is None:continue
        if not isinstance(obj,dict):record(name,'JSON shape','object',type(obj).__name__,False);continue
        missing=sorted(keys-set(obj));extra=len(set(obj)-keys)
        if missing or extra:record(name,'schema','required fields '+', '.join(sorted(keys)),'missing '+', '.join(missing)+f'; {extra} unknown field(s), names suppressed',False)
    run=objects.get('private/run.json');identity=objects.get('run-id.json');context=objects.get('private/context.json');catalog=objects.get('private/catalog.json')
    if isinstance(run,dict):
        fixed={'root':'/evidence/run','database_host':'127.0.0.1','service_file':'/private/services.conf','catalog_file':'/private/catalog.json','context_file':'/private/context.json','paypal_secret_file':'/private/credentials.json','admin_service':'item6-admin','tenant_service':'item6-tenant','payment_service':'item6-payment'}
        for key,value in fixed.items():
            if key in run and run[key]!=value:record('private/run.json:'+key,'isolated destination',value,'different value (not printed)',False)
        if isinstance(identity,dict) and run.get('database_name')!=identity.get('database_name'):record('private/run.json:database_name','run identity','same as run-id.json','different value (not printed)',False)
    if isinstance(identity,dict):
        from single_config import root_check
        try:root_check(root)
        except Exception:record('run-id.json / evidence','identity safety','fresh valid run ID/database, false run_started/launch_authorized','identity or fresh-directory check rejected; inspect locally',False)
    row=None
    if catalog is not None:
        if not isinstance(catalog,list) or len(catalog)!=1 or not isinstance(catalog[0],dict):record('private/catalog.json','catalog shape','list containing one model object',type(catalog).__name__+' with wrong structure',False)
        else:row=catalog[0]
    if row is not None:
        required={'provider','model','price_version','source','expense_input','expense_output','expense_cache_hit','expense_cache_miss','expense_units_per_usd','retail_input','retail_output','retail_units_per_credit','usage_categories'}
        missing=sorted(required-set(row))
        if missing:record('private/catalog.json[0]','schema','all scaffold catalog fields','missing '+', '.join(missing),False)
        for key,value in [('provider','deepseek'),('retail_units_per_credit',1000)]:
            if row.get(key)!=value:record('private/catalog.json[0]:'+key,'fixed provider/scale',str(value),'different value (not printed)',False)
        if row.get('expense_input') is not None and row.get('expense_cache_miss') is not None and row['expense_input']!=row['expense_cache_miss']:record('private/catalog.json[0]:expense_input','repeated price','same as expense_cache_miss','different values (not printed)',False)
        for key in ('expense_cache_hit','expense_cache_miss','expense_output'):
            rate=row.get(key);unit=row.get('expense_units_per_usd')
            if rate is not None and (type(rate) is not int or type(unit) is not int or unit<=0):record('private/catalog.json[0]:'+key,'fixed-point import','integer rate with positive integer expense_units_per_usd, or null for unresolved','rate type '+type(rate).__name__+'; scale type '+type(unit).__name__+' (values suppressed)',False)
    for where,obj in [('private/run.json',run),('private/context.json',context),('private/catalog.json[0]',row)]:
        if isinstance(obj,dict) and 'model' in obj:
            from single_config import unresolved
            if not unresolved(obj['model']) and obj['model']!='deepseek-flash':record(where+':model','selected model','deepseek-flash or unresolved placeholder','different value (not printed)',False)
    # Service parser errors can include password-looking text, so report only paths/types.
    p=root/'private/services.conf'
    if p.is_file() and not any(q.is_symlink() for q in (p,*p.parents)) and p.stat().st_mode&0o077==0 and p.stat().st_size<=1024*1024 and isinstance(run,dict) and isinstance(identity,dict):
        import configparser
        from isolation import parse_loopback_service
        try:
            cfg=configparser.ConfigParser(interpolation=None);cfg.read_string(p.read_text())
            for field,user in [('admin_service','requi'),('tenant_service','delivery_app'),('payment_service','delivery_payment_test')]:
                label=run.get(field)
                expected={'host':'127.0.0.1','database_name':identity.get('database_name'),'port':55439}
                if parse_loopback_service(p.read_text(),label)!=expected or cfg[label].get('user')!=user:record('private/services.conf:'+field,'database destination/role',f'loopback:55439, scaffold database, user {user}','different destination or role (not printed)',False)
        except Exception:record('private/services.conf','service syntax/labels','three scaffold service labels, fixed loopback roles','parse/label/destination rejection; values suppressed',False)
    failures=[c for c in checks if not c['ok']]
    return {'action':'diagnose-import','import_ready':not failures,'checks':failures or [{'checked':'legacy import preflight','expected':'fresh compatible private files','found':'checks passed; no values printed','where':str(root),'ok':True}],
        'next':'Fix only reported fields locally; preserve existing keys/files/evidence. No secrets or raw configs in chat.' if failures else 'Run import-config once; no credentials or values were displayed.',
        'run_started':False,'files_changed':False}
