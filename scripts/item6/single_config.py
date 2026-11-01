"""Single human-edit source; old-image JSON is generated compatibility output.

.env values use JSON scalar syntax. No shell evaluation, interpolation or exec.
Review records exact source bytes privately; prints only non-secret values.
"""
from decimal import Decimal
from pathlib import Path
import configparser,hashlib,json,os,re,uuid
from isolation import RunIsolation,parse_loopback_service

SECRET={'PAYPAL_CLIENT_ID','PAYPAL_CLIENT_SECRET','DEEPSEEK_API_KEY'}
FIELDS=SECRET|{'MODEL','SANDBOX_MERCHANT_ID','SANDBOX_WEBHOOK_ID','PRICE_SOURCE','CONTEXT_SOURCE','PRICING_BAND',
    'INPUT_CACHE_HIT_USD_PER_MILLION','INPUT_CACHE_MISS_USD_PER_MILLION','OUTPUT_USD_PER_MILLION',
    'CONTEXT_TOKENS','MAX_OUTPUT_TOKENS','RETAIL_INPUT_UNITS_PER_TOKEN','RETAIL_OUTPUT_UNITS_PER_TOKEN'}
LEGACY_FIELDS=set(FIELDS)
FIELDS=SECRET|{'MODEL','SANDBOX_MERCHANT_ID','SANDBOX_WEBHOOK_ID'}
OPTIONAL_FIELDS={'PRICE_PER_TOKEN','PRICE_PER_10MS','USD_PER_CREDIT'}


def private_json(path):
    p=Path(path)
    if p.is_symlink() or not p.is_file() or p.stat().st_mode&0o077 or p.stat().st_size>1024*1024:raise ValueError('private bounded file required')
    return json.loads(p.read_text())


def root_check(root):
    root=Path(root).absolute()
    if any(p.is_symlink() for p in (root,*root.parents)) or not root.is_dir() or root.stat().st_mode&0o077:raise ValueError('private run directory required')
    for name in ('private','evidence'):
        p=root/name
        if p.is_symlink() or not p.is_dir() or p.stat().st_mode&0o077:raise ValueError('private child directory required')
    if any((root/'evidence').iterdir()):raise ValueError('fresh empty evidence required; never compile a partial run')
    identity=private_json(root/'run-id.json')
    if set(identity)!={'run_id','database_name','run_started','launch_authorized'} or identity['run_started'] is not False or identity['launch_authorized'] is not False:raise ValueError('exact fresh run identity required')
    if not re.fullmatch('[a-f0-9]{32}',identity['run_id']) or not re.fullmatch('requi_item6_[a-z0-9_]{8,60}',identity['database_name']):raise ValueError('isolated identity required')
    return root,identity


def write_env(path,data):
    if not FIELDS<=set(data) or set(data)-(FIELDS|OPTIONAL_FIELDS|LEGACY_FIELDS):raise ValueError('exact single-source fields required')
    fd=os.open(path,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
    with os.fdopen(fd,'w') as f:
        f.write('# Only human-edited run configuration. JSON scalars; no shell expansion.\n# null means unresolved. Never source this file into a shell.\n')
        for k in sorted(data):f.write(k+'='+json.dumps(data[k],ensure_ascii=True)+'\n')


def load_env(root):
    p=root/'private/.env'
    if p.is_symlink() or not p.is_file() or p.stat().st_mode&0o077 or p.stat().st_size>1024*1024:raise ValueError('private bounded .env required')
    raw=p.read_bytes();values={}
    for line in raw.decode().splitlines():
        if not line.strip() or line.lstrip().startswith('#'):continue
        key,sep,value=line.partition('=')
        if not sep or key not in FIELDS|OPTIONAL_FIELDS|LEGACY_FIELDS or key in values:raise ValueError('unknown or duplicate .env field')
        try:decoded=json.loads(value)
        except ValueError:raise ValueError('JSON scalar syntax required; no interpolation') from None
        if not isinstance(decoded,(str,int,type(None))) or isinstance(decoded,bool):raise ValueError('JSON string/integer/null required')
        values[key]=decoded
    if not FIELDS<=set(values) or set(values)-(FIELDS|OPTIONAL_FIELDS|LEGACY_FIELDS):raise ValueError('exact single-source fields required')
    return values,hashlib.sha256(raw).hexdigest()


def unresolved(v):return v is None or isinstance(v,str) and (not v.strip() or 'REPLACE' in v or v=='PLACEHOLDER')


def create_single(root):
    root=Path(root).absolute()
    if root.exists() or any(p.is_symlink() for p in (root,*root.parents)):raise ValueError('new non-symlink directory required')
    root.mkdir(mode=0o700);(root/'private').mkdir(mode=0o700);(root/'evidence').mkdir(mode=0o700)
    run_id=uuid.uuid4().hex
    RunIsolation.write(root/'run-id.json',{'run_id':run_id,'database_name':'requi_item6_'+run_id,'run_started':False,'launch_authorized':False})
    return template(root)


def template(root):
    root,identity=root_check(root);data={k:None for k in FIELDS};data['MODEL']='deepseek-flash'
    write_env(root/'private/.env',data)
    return {'single_source':'private/.env','run_started':False,'secrets_printed':False}


def import_legacy(root):
    root,identity=root_check(root);p=root/'private'
    run=private_json(p/'run.json');credentials=private_json(p/'credentials.json');catalog=private_json(p/'catalog.json');context=private_json(p/'context.json')
    required={'root','database_name','database_host','service_file','admin_service','tenant_service','payment_service','model','catalog_file','context_file','paypal_secret_file','verified_sandbox_merchant','sandbox_webhook_id','operator_registry_version'}
    if set(run)!=required or set(credentials)!={'client_id','client_secret','deepseek_api_key'}:raise ValueError('exact legacy schema required')
    fixed={'root':'/evidence/run','database_host':'127.0.0.1','service_file':'/private/services.conf','catalog_file':'/private/catalog.json','context_file':'/private/context.json','paypal_secret_file':'/private/credentials.json','admin_service':'item6-admin','tenant_service':'item6-tenant','payment_service':'item6-payment'}
    if any(run[k]!=v for k,v in fixed.items()) or run['database_name']!=identity['database_name']:raise ValueError('legacy destinations conflict with scaffold identity')
    service=p/'services.conf'
    if service.is_symlink() or service.stat().st_mode&0o077 or service.stat().st_size>1024*1024:raise ValueError('private legacy services required')
    text=service.read_text();cfg=configparser.ConfigParser(interpolation=None);cfg.read_string(text)
    for label,user in [('admin_service','requi'),('tenant_service','delivery_app'),('payment_service','delivery_payment_test')]:
        if parse_loopback_service(text,run[label])!={'host':'127.0.0.1','database_name':identity['database_name'],'port':55439} or cfg[run[label]]['user']!=user:raise ValueError('legacy database/model destinations conflict')
    if not isinstance(catalog,list) or len(catalog)!=1:raise ValueError('one exact selected catalog model required')
    row=catalog[0]
    models=[v for v in (run['model'],row['model'],context['model']) if not unresolved(v)]
    if any(v!='deepseek-flash' for v in models):raise ValueError('legacy models conflict with selected deepseek-flash')
    if row.get('expense_input') is not None and row.get('expense_cache_miss') is not None and row['expense_input']!=row['expense_cache_miss']:raise ValueError('legacy repeated input prices conflict')
    if row['provider']!='deepseek' or row['retail_units_per_credit']!=1000:raise ValueError('legacy provider/retail scale conflicts')
    data={k:None for k in FIELDS};data.update(MODEL='deepseek-flash',PAYPAL_CLIENT_ID=credentials['client_id'],PAYPAL_CLIENT_SECRET=credentials['client_secret'],DEEPSEEK_API_KEY=credentials['deepseek_api_key'],
        SANDBOX_MERCHANT_ID=run['verified_sandbox_merchant'],SANDBOX_WEBHOOK_ID=run['sandbox_webhook_id'],
        PRICE_SOURCE=row['source'],CONTEXT_SOURCE=context['source'],CONTEXT_TOKENS=context['context_tokens'],MAX_OUTPUT_TOKENS=context['maximum_output_tokens'],
        RETAIL_INPUT_UNITS_PER_TOKEN=row['retail_input'],RETAIL_OUTPUT_UNITS_PER_TOKEN=row['retail_output'])
    unit=row.get('expense_units_per_usd')
    for target,source in [('INPUT_CACHE_HIT_USD_PER_MILLION','expense_cache_hit'),('INPUT_CACHE_MISS_USD_PER_MILLION','expense_cache_miss'),('OUTPUT_USD_PER_MILLION','expense_output')]:
        rate=row.get(source)
        if rate is not None:
            if type(rate) is not int or type(unit) is not int or unit<=0:raise ValueError('explicit legacy fixed-point scale required')
            data[target]=format(Decimal(rate)*1000000/Decimal(unit),'f')
    # Effective time/band was absent from legacy schema; never infer it from a rate.
    data={k:None if unresolved(v) else v for k,v in data.items()}
    write_env(p/'.env',data)
    return {'single_source':'private/.env','legacy_preserved':True,'unresolved_fields':sorted(k for k,v in data.items() if unresolved(v)),'reviewed':False,'secrets_printed':False,'run_started':False}


def apply_proposal(root,band=None,context_tokens=None):
    raise ValueError('manual band/context application retired; use fetch-vendor-snapshot and migrate-d13')


def build(root,data,identity):
    from vendor_config import build_runtime
    return build_runtime(root,data,identity)


def review(root,confirm=None):
    root,identity=root_check(root);data,source_hash=load_env(root);build(root,data,identity)
    public={k:v for k,v in data.items() if k not in SECRET}
    runtime=build(root,data,identity)
    snapshot_hash=hashlib.sha256(json.dumps(runtime['catalog.json'],sort_keys=True).encode()).hexdigest()
    digest=hashlib.sha256(json.dumps({'values':public,'identity':identity,'snapshot_hash':snapshot_hash},sort_keys=True).encode()).hexdigest()
    if confirm is None:return {'review_hash':digest,'nonsecret_values':public,'credentials':'present, not displayed','reviewed':False,'run_started':False,
        'review_conditions':['generated vendor source/hash/time snapshot reviewed once','existing optional retail defaults/overrides','128000 owner protective ceiling is not a vendor fact','sandbox bindings verified manually; authentic webhook still required','rebuilt D13 image required; verified request bound remains required']}
    if confirm!=digest:raise ValueError('exact current review hash required')
    target=root/'private'/('review-'+digest+'-'+source_hash[:16]+'.json')
    RunIsolation.write(target,{'review_hash':digest,'source_sha256':source_hash,'identity':identity,'confirmed_locally':True,'launch_authorized':False})
    return {'review_file':str(target),'review_hash':digest,'reviewed':True,'run_started':False,'launch_authorized':False}


def attest_config(root,*,prompt=input,output=None):
    """Attest the locally displayed config only. Never authorize execution."""
    import sys
    output=output or sys.stdout
    root,identity=root_check(root)
    _,source_hash=load_env(root)
    facts=review(root)
    print(json.dumps(facts,indent=2),file=output,flush=True)
    print('Config review only. This does not authorize a run, spending or any other gate.',file=output,flush=True)
    try:answer=prompt('Confirm this exact config review? Type "yes", or Enter to leave unreviewed: ')
    except EOFError:answer=''
    if answer.strip()!='yes':
        return {'reviewed':False,'run_started':False,'launch_authorized':False,'files_changed':False}
    current_root,current_identity=root_check(root)
    _,current_hash=load_env(root)
    current=review(root)
    if current_identity!=identity or current_hash!=source_hash or current['review_hash']!=facts['review_hash']:
        raise ValueError('configuration changed during review; inspect it again')
    digest=facts['review_hash']
    target=root/'private'/('review-'+digest+'-'+source_hash[:16]+'.json')
    RunIsolation.write(target,{'review_hash':digest,'source_sha256':source_hash,'identity':identity,'confirmed_locally':True,'launch_authorized':False})
    return {'review_file':str(target),'review_hash':digest,'reviewed':True,'run_started':False,'launch_authorized':False}


def compile_config(root):
    root,identity=root_check(root);data,source_hash=load_env(root);facts=review(root);digest=facts['review_hash']
    review_path=root/'private'/('review-'+digest+'-'+source_hash[:16]+'.json');receipt=private_json(review_path)
    if receipt!={'review_hash':digest,'source_sha256':source_hash,'identity':identity,'confirmed_locally':True,'launch_authorized':False}:raise ValueError('exact unchanged local review required')
    runtime=root/'private'/('runtime-'+uuid.uuid4().hex);runtime.mkdir(mode=0o700)
    for name,body in build(root,data,identity).items():RunIsolation.write(runtime/name,body)
    fd=os.open(runtime/'services.conf',os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
    with os.fdopen(fd,'w') as f:
        for label,user in [('admin','requi'),('tenant','delivery_app'),('payment','delivery_payment_test')]:f.write(f'[item6-{label}]\nhost=127.0.0.1\nport=55439\ndbname={identity["database_name"]}\nuser={user}\n\n')
    # Frozen source is retained privately; never mount the editable .env/repo.
    RunIsolation.write(runtime/'compiled-receipt.json',{'source_sha256':source_hash,'review_hash':digest,'run_id':identity['run_id'],'launch_authorized':False})
    launcher=root/('run-compiled-'+runtime.name.removeprefix('runtime-')+'.sh')
    relative=runtime.relative_to(root)
    verifier=root/'private'/('verify-'+runtime.name.removeprefix('runtime-')+'.py')
    # Standalone standard-library check works on Mac without installed packages.
    verification='''import hashlib,json,pathlib,sys
root=pathlib.Path(sys.argv[1]);runtime=root/'RUNTIME'
source=root/'private/.env'
identity=root/'run-id.json';evidence=root/'evidence'
if identity.is_symlink() or identity.stat().st_mode&0o077 or hashlib.sha256(identity.read_bytes()).hexdigest()!='IDENTITY_HASH':raise SystemExit('Run identity changed; do not run')
if evidence.is_symlink() or evidence.stat().st_mode&0o077 or any(evidence.iterdir()):raise SystemExit('Existing evidence; preserve prior run, do not retry')
if source.is_symlink() or source.stat().st_mode&0o077 or hashlib.sha256(source.read_bytes()).hexdigest()!='SOURCE_HASH':raise SystemExit('Source changed or insecure; review and compile again')
for name,digest in HASHES.items():
 p=runtime/name
 if p.is_symlink() or p.stat().st_mode&0o077 or hashlib.sha256(p.read_bytes()).hexdigest()!=digest:raise SystemExit('Compiled snapshot changed; do not run')
'''.replace('RUNTIME',str(relative)).replace('SOURCE_HASH',source_hash).replace('IDENTITY_HASH',hashlib.sha256((root/'run-id.json').read_bytes()).hexdigest())
    hashes={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in runtime.iterdir()}
    verification=verification.replace('HASHES',repr(hashes))
    fd=os.open(verifier,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
    with os.fdopen(fd,'w') as f:f.write(verification)
    text='''#!/usr/bin/env bash
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
if [ "$#" -gt 0 ]; then
 [ "$#" -eq 2 ] && [ "$1" = --go ] && [ "$2" = RUN_ID ] || { echo 'Exact separately approved run ID required'; exit 2; }
fi
python3 "$HERE/VERIFIER" "$HERE"
docker run --name "requi-item6-RUN_ID" -it --read-only --cap-drop ALL --security-opt no-new-privileges --tmpfs /tmp:rw,nosuid,nodev,size=2g \
 --mount "type=bind,src=$HERE/RUNTIME,dst=/private,readonly" \
 --mount "type=bind,src=$HERE/evidence,dst=/evidence" \
 requi-item6-test --config /private/run.json --run-id RUN_ID "$@"
'''.replace('RUN_ID',identity['run_id']).replace('RUNTIME',str(relative)).replace('VERIFIER',str(verifier.relative_to(root)))
    fd=os.open(launcher,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o700)
    with os.fdopen(fd,'w') as f:f.write(text)
    return {'generated_runtime':str(runtime),'dry_launcher':str(launcher),'source':'private/.env','run_started':False,'launch_authorized':False,'old_files_preserved':True}
