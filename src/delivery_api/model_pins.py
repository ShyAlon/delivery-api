"""One validator/resolver for public reviewed model pins. No keys or automatic approvals."""
import copy,datetime,hashlib,json
from . import production_gateway as pg,vendor_sources as vs
from .provider_snapshot import ProviderSnapshot


def revision(payload):return hashlib.sha256(json.dumps(payload,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def validate(payload):
    if not isinstance(payload,dict) or set(payload)!={'row','context'}:raise ValueError('A price row and context are required')
    value=copy.deepcopy(payload);row=value['row'];context=value['context']
    text=json.dumps(value)
    if len(text)>50000:raise ValueError('Pin exceeds size bound')
    allowed={'provider','model','price_version','source','expense_input','expense_output','expense_cache_hit','expense_cache_miss','expense_units_per_usd','retail_input','retail_output','retail_units_per_credit','usage_categories','retail_formula','provider_snapshot'}
    if set(row)-allowed:raise ValueError('Unexpected price fields; pins never contain credentials')
    env=environment([value]);env.update(DELIVERY_LOCAL_PREVIEW='1',DEEPSEEK_API_KEY='offline-validation-only')
    if row.get('provider')=='ollama':env.pop(pg.KEY_ENV,None)
    gateway=pg.ProductionGateways(None,env);gateway.close()
    if row['provider']=='deepseek':ProviderSnapshot(row['provider_snapshot'])
    return value


def environment(pins):
    rows=[p['row'] for p in pins];contexts=[dict(p['context'],provider=p['row']['provider']) for p in pins]
    if not rows:raise ValueError('No reviewed model pins. Open Back office > Model pins to review and approve.')
    if len(rows)==1:contexts=copy.deepcopy(pins[0]['context'])
    return {pg.PRICES_ENV:json.dumps(rows),pg.CEILING_ENV:json.dumps(contexts)}


def refresh(payload,fetcher=None):
    value=validate(payload);row=value['row']
    if row['provider']=='ollama':return {'payload':value,'warning':'Local model has no vendor price. Edit bounds after checking the local server.','changed':False}
    fetcher=fetcher or vs.fetch
    snapshot=row['provider_snapshot']
    try:
        bodies={url:vs.fetch_verified_source(url,fetcher) for url in (vs.PRICING_URL,vs.API_URL)}
        parsed=vs.parse(bodies[vs.PRICING_URL],bodies[vs.API_URL],row['model'])
    except Exception as exc:
        checked=ProviderSnapshot(snapshot);now=datetime.datetime.now(datetime.timezone.utc);checked.fresh(now)
        if now-checked.fetched>=datetime.timedelta(hours=24):raise ValueError('Stored price is at least 24h old and refresh failed: '+str(exc)) from exc
        return {'payload':value,'warning':'Refresh failed; stored price is under 24h old: '+str(exc),'changed':False}
    snap={k:parsed[k] for k in ('model','model_version','published_usd_per_million','published_period_rule','raw_context_limit','raw_output_limit','context_proposal')}
    snap.update(parser_version='deepseek-html-v2',fetched_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),sources=[{'url':u,'sha256':hashlib.sha256(b.encode()).hexdigest()} for u,b in bodies.items()])
    peak=parsed['catalog_proposals']['peak'][0]
    for key in ('expense_cache_hit','expense_cache_miss','expense_output','expense_units_per_usd','usage_categories'):row[key]=peak[key]
    row['expense_input']=peak['expense_cache_miss'];row['provider_snapshot']=snap
    row['price_version']='pin-'+revision(value)[:16]
    # Bounds are reviewed, not silently raised by a fetch.
    proposed=parsed['context_proposal']
    if value['context']['maximum_output_tokens']>proposed['maximum_output_tokens']:raise ValueError('Stored output bound exceeds newly published limit; edit and review bounds')
    return {'payload':validate(value),'warning':None,'changed':value!=payload}


def badge(payload):
    snap=payload['row'].get('provider_snapshot');fetched=snap.get('fetched_at') if snap else None
    stale=bool(fetched and datetime.datetime.now(datetime.timezone.utc)-ProviderSnapshot(snap).fetched>=datetime.timedelta(hours=24))
    return {'fetched_at':fetched,'stale':stale,'badge':'Stale - refresh needed' if stale else 'Local - no provider price' if not fetched else 'Current'}


class PinRepository:
    def __init__(self,db,principal=None):self.db=db;self.principal=principal
    def list(self):
        with public_pin_context(),self.db.transaction() as conn:
            return conn.execute('SELECT r.* FROM public.model_pin_heads h JOIN public.model_pin_revisions r USING(revision) ORDER BY r.provider,r.model').fetchall()
    def commit(self,store,payload,expected,reason):
        value=validate(payload)
        if not isinstance(reason,str) or not 1<=len(reason.strip())<=400:raise ValueError('A reason of 1 to 400 characters is required')
        authority=self.principal.authorize(store,'services.operate');row=value['row'];rev=revision(value)
        with self.db.transaction() as conn:
            conn.execute('SET LOCAL ROLE requi_model_pin_editor')
            if not conn.execute('SELECT public.requi_operator_read_allowed(%s,%s) AS allowed',('services.operate',authority['registry_version'])).fetchone()['allowed']:raise PermissionError('Operator registry denied model pin write')
            conn.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',('model-pin:'+row['provider']+':'+row['model'],))
            head=conn.execute('SELECT revision FROM public.model_pin_heads WHERE provider=%s AND model=%s',(row['provider'],row['model'])).fetchone()
            if (head['revision'] if head else None)!=expected:raise ValueError('Pin changed. Refresh the page and review the current diff.')
            if head and head['revision']==rev:return rev
            fetched=value['row'].get('provider_snapshot',{}).get('fetched_at')
            conn.execute('INSERT INTO public.model_pin_revisions(revision,provider,model,payload,fetched_at,operator_subject,reason,previous_revision) VALUES(%s,%s,%s,%s::jsonb,%s,%s,%s,%s)',(rev,row['provider'],row['model'],json.dumps(value),fetched,authority['operator_subject'],reason.strip(),expected))
            conn.execute('INSERT INTO public.model_pin_heads VALUES(%s,%s,%s) ON CONFLICT(provider,model) DO UPDATE SET revision=excluded.revision',(row['provider'],row['model'],rev))
        return rev
    def history(self,store):
        authority=self.principal.authorize(store,'services.operate')
        with self.db.transaction() as conn:
            conn.execute('SET LOCAL ROLE requi_model_pin_editor')
            if not conn.execute('SELECT public.requi_operator_read_allowed(%s,%s) AS allowed',('services.operate',authority['registry_version'])).fetchone()['allowed']:raise PermissionError('Operator registry denied model pin history')
            return conn.execute('SELECT revision,provider,model,payload,fetched_at,created_at,operator_subject,reason,previous_revision FROM public.model_pin_revisions ORDER BY created_at DESC LIMIT 100').fetchall()


def selected_environment(db,environ):
    """Read durable heads; configuration selects routes, never supplies price defaults."""
    pins=PinRepository(db).list()
    mode=environ.get('REQUI_PIN_PROVIDERS','deepseek').split(',')
    selected=[]
    for provider in mode:
        model=environ.get('DEEPSEEK_MODEL' if provider=='deepseek' else 'DELIVERY_OLLAMA_MODEL')
        candidates=[r for r in pins if r['provider']==provider and (not model or r['model']==model)]
        if len(candidates)!=1:raise ValueError('Choose one reviewed '+provider+' model in Back office > Model pins; no implicit fallback')
        selected.append(candidates[0]['payload'])
    return environment(selected)


class DurableGateways:
    """One immutable reviewed pin set per task generation, restored for every job."""
    def __init__(self,store,environ):
        self.store=store;self.environ=dict(environ);self._factories={}
    def _factory(self,env):
        key=revision(env)
        if key not in self._factories:self._factories[key]=pg.ProductionGateways(self.store,{**self.environ,**env})
        return self._factories[key]
    @property
    def models(self):return self._factory(selected_environment(self.store._postgres,self.environ)).models
    @property
    def verify_vendor(self):
        return self._factory(selected_environment(self.store._postgres,self.environ)).verify_vendor
    def for_task(self,task_id):
        task=self.store.task(task_id)
        if not task:raise KeyError(task_id)
        name='model-pins:'+str(task['generation']);old=self.store.decision_review(task_id,name,'model-pins-v1')
        if old is None:
            self.store.retain_decision_review(task_id,name,'model-pins-v1',selected_environment(self.store._postgres,self.environ))
            old=self.store.decision_review(task_id,name,'model-pins-v1')
        return self._factory(old)
    def __call__(self,capability,job_id):
        job=self.store.worker_job(job_id)
        if not job:raise KeyError(job_id)
        return self.for_task(job['task_id'])(capability,job_id)
    def accepts(self,provider,model):
        # Existing immutable factories are accessible only through task-owned job resolution.
        return any(factory.models.get(provider)==model for factory in self._factories.values())
    def close(self):
        for factory in self._factories.values():factory.close()


def public_pin_context():
    """Public config reads only. No operator/user impersonation or private data access."""
    from .tenancy import TenantScope,tenant_context
    return tenant_context(TenantScope('00000000-0000-0000-0000-000000000000'))

# Short-lived in-process signed proposals bind approval to a server-fetched diff.
# Restart invalidates proposals; the user refreshes again, never an automatic write.
import hmac,secrets,time
_PROPOSAL_KEY=secrets.token_bytes(32)


def proposal(payload,expected,subject):
    value={'payload':payload,'expected':expected,'subject':subject,'expires':time.time()+900}
    raw=json.dumps(value,sort_keys=True,separators=(',',':'))
    return {'review':value,'signature':hmac.new(_PROPOSAL_KEY,raw.encode(),'sha256').hexdigest()}


def approved_proposal(review,signature,subject):
    raw=json.dumps(review,sort_keys=True,separators=(',',':'))
    expected=hmac.new(_PROPOSAL_KEY,raw.encode(),'sha256').hexdigest()
    if not hmac.compare_digest(expected,signature) or review['subject']!=subject or time.time()>=review['expires']:raise ValueError('Review expired or changed. Refresh and review again.')
    return validate(review['payload'])


def new_deepseek(model,context=128000,maximum_output=16384,fetcher=None):
    """Fetch a proposal for a new model, never a persisted or auto-approved default."""
    from .retail_pricing import DEFAULTS
    from decimal import Decimal
    fetcher=fetcher or vs.fetch
    bodies={url:vs.fetch_verified_source(url,fetcher) for url in (vs.PRICING_URL,vs.API_URL)}
    parsed=vs.parse(bodies[vs.PRICING_URL],bodies[vs.API_URL],model)
    snap={k:parsed[k] for k in ('model','model_version','published_usd_per_million','published_period_rule','raw_context_limit','raw_output_limit','context_proposal')}
    snap.update(parser_version='deepseek-html-v2',fetched_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),sources=[{'url':u,'sha256':hashlib.sha256(b.encode()).hexdigest()} for u,b in bodies.items()])
    peak=parsed['catalog_proposals']['peak'][0];retail=int(Decimal(DEFAULTS['PRICE_PER_TOKEN'])*10**9)
    row={'provider':'deepseek','model':model,'price_version':'review-'+snap['fetched_at'],'source':vs.PRICING_URL,
        'expense_input':peak['expense_cache_miss'],'expense_output':peak['expense_output'],'expense_cache_hit':peak['expense_cache_hit'],'expense_cache_miss':peak['expense_cache_miss'],'expense_units_per_usd':peak['expense_units_per_usd'],'usage_categories':peak['usage_categories'],
        'retail_input':retail,'retail_output':retail,'retail_units_per_credit':10**9,'provider_snapshot':snap,
        'retail_formula':{'formula_version':'tokens-plus-compute-v1','units_per_credit':10**9,'price_per_token':DEFAULTS['PRICE_PER_TOKEN'],'price_per_10ms':DEFAULTS['PRICE_PER_10MS'],'usd_per_credit':DEFAULTS['USD_PER_CREDIT']}}
    bound={'model':model,'context_tokens':context,'maximum_output_tokens':maximum_output,'source':vs.PRICING_URL,'bound_version':row['price_version'],'protective_run_ceiling':True}
    return validate({'row':row,'context':bound})
