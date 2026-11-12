"""One validator/resolver for public reviewed model pins. No keys or automatic approvals."""
import copy,datetime,hashlib,json
from . import production_gateway as pg,vendor_sources as vs
from .provider_snapshot import ProviderSnapshot


def revision(payload):return hashlib.sha256(json.dumps(payload,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def validate(payload):
    if not isinstance(payload,dict) or set(payload)!={'row','context'}:raise ValueError('A price row and context are required')
    value=copy.deepcopy(payload);row=value['row'];context=value['context']
    if row.get('provider')=='deepseek':
        snap=ProviderSnapshot(row['provider_snapshot']);snap.fresh(datetime.datetime.now(datetime.timezone.utc))
        raw=snap.data.get('raw_context_limit')
        if raw:
            from decimal import Decimal
            limit=Decimal(raw[:-1])*{'K':1000,'M':1000000}[raw[-1]]
            if context['context_tokens']>limit:raise ValueError('Context exceeds published vendor limit')
        output=snap.data.get('context_proposal',{}).get('maximum_output_tokens')
        if output and context['maximum_output_tokens']>output:raise ValueError('Output exceeds published vendor limit')
        ceiling=snap.ceiling()
        if any(row.get(k)!=v for k,v in [('expense_input',ceiling['cache_miss']),('expense_cache_miss',ceiling['cache_miss']),('expense_cache_hit',ceiling['cache_hit']),('expense_output',ceiling['output'])]):raise ValueError('Price row must match its source snapshot rates')
    def no_secrets(item):
        import re
        if isinstance(item,dict):
            for key,child in item.items():
                if re.search(r'password|passcode|secret|authorization|cookie|api[_-]?key|credential|private[_-]?key|access[_-]?token',key,re.I):raise ValueError('Pins never contain credentials')
                no_secrets(child)
        elif isinstance(item,list):
            for child in item:no_secrets(child)
    no_secrets(value)
    text=json.dumps(value)
    if len(text)>50000:raise ValueError('Pin exceeds size bound')
    allowed={'provider','model','price_version','source','expense_input','expense_output','expense_cache_hit','expense_cache_miss','expense_units_per_usd','retail_input','retail_output','retail_units_per_credit','usage_categories','retail_formula','provider_snapshot'}
    if set(row)-allowed:raise ValueError('Unexpected price fields; pins never contain credentials')
    env=environment([value]);env.update(DEEPSEEK_API_KEY='offline-validation-only')
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
            return conn.execute('SELECT r.revision,r.provider,r.model,r.payload,r.fetched_at,h.is_default FROM public.model_pin_heads h JOIN public.model_pin_revisions r USING(revision) ORDER BY r.provider,r.model').fetchall()
    def commit(self,store,payload,expected,reason):
        value=validate(payload)
        if not isinstance(reason,str) or not 1<=len(reason.strip())<=400:raise ValueError('A reason of 1 to 400 characters is required')
        authority=self.principal.authorize(store,'services.operate');row=value['row'];rev=__import__('uuid').uuid4().hex
        with self.db.transaction() as conn:
            conn.execute('SET LOCAL ROLE requi_model_pin_editor')
            if not conn.execute('SELECT public.requi_operator_read_allowed(%s,%s) AS allowed',('services.operate',authority['registry_version'])).fetchone()['allowed']:raise PermissionError('Operator registry denied model pin write')
            conn.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',('model-pin:'+row['provider'],))
            head=conn.execute('SELECT revision FROM public.model_pin_heads WHERE provider=%s AND model=%s',(row['provider'],row['model'])).fetchone()
            if (head['revision'] if head else None)!=expected:raise ValueError('Pin changed. Refresh the page and review the current diff.')
            fetched=value['row'].get('provider_snapshot',{}).get('fetched_at')
            conn.execute('INSERT INTO public.model_pin_revisions(revision,provider,model,payload,fetched_at,operator_subject,reason,previous_revision) VALUES(%s,%s,%s,%s::jsonb,%s,%s,%s,%s)',(rev,row['provider'],row['model'],json.dumps(value),fetched,authority['operator_subject'],reason.strip(),expected))
            conn.execute('UPDATE public.model_pin_heads SET is_default=false WHERE provider=%s',(row['provider'],))
            conn.execute('INSERT INTO public.model_pin_heads(provider,model,revision,is_default) VALUES(%s,%s,%s,true) ON CONFLICT(provider,model) DO UPDATE SET revision=excluded.revision,is_default=true',(row['provider'],row['model'],rev))
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
        candidates=[r for r in pins if r['provider']==provider and r['is_default']]
        if len(candidates)!=1:raise ValueError('Choose one reviewed '+provider+' model in Back office > Model pins; no implicit fallback')
        selected.append(candidates[0]['payload'])
    return environment(selected)


class NoApprovedModelPin(ValueError):
    code='no_approved_model_pin'


class DurableGateways:
    """One immutable reviewed pin set per task generation, restored for every job."""
    def __init__(self,store,environ):
        import threading
        self.store=store;self.environ=dict(environ);self._factories={};self._lock=threading.RLock();self._launch_errors={};self.route_errors={}
    def _factory(self,env):
        key=revision({k:json.loads(v) for k,v in env.items()})
        with self._lock:
            if key not in self._factories:
                config={**self.environ,**env}
                if {r['provider'] for r in json.loads(env[pg.PRICES_ENV])}=={'ollama'}:config.pop(pg.KEY_ENV,None)
                factory=pg.ProductionGateways(self.store,config)
                rows=json.loads(env[pg.PRICES_ENV]);contexts=json.loads(env[pg.CEILING_ENV])
                for row in rows:
                    if row['provider']!='deepseek':continue
                    context=next(c for c in contexts if c['provider']=='deepseek') if isinstance(contexts,list) else contexts
                    context=dict(context);context.pop('provider',None)
                    verify=pin_verifier({'row':row,'context':context})
                    child=factory._providers['deepseek'] if hasattr(factory,'_providers') else factory
                    child.verify_vendor=verify;factory.verify_vendor=verify
                self._factories[key]=factory
        return self._factories[key]
    def registry(self):
        from .model_registry import discover
        return discover(PinRepository(self.store._postgres).list(),self.environ)
    def available_environment(self):
        """Resolve each explicitly configured provider independently, never a substitute model.

        A missing or invalid paid pin must not erase an independently valid local
        route. Unavailable providers remain named in route_errors and cannot dispatch.
        """
        rows=self.registry();selected=[];errors={}
        if not rows:
            self.route_errors={provider:'No approved model pin' for provider in self.environ.get('REQUI_PIN_PROVIDERS','deepseek').split(',')}
            raise NoApprovedModelPin('No ready reviewed route: no compatible approved model or verified local package.')
        for provider in ('deepseek','ollama'):
            try:
                if provider in self._launch_errors:raise ValueError(self._launch_errors[provider])
                candidates=[r for r in rows if r['provider']==provider and r['is_default']]
                if len(candidates)!=1:raise ValueError('Choose one reviewed '+provider+' model in Back office > Model pins; no implicit fallback')
                payload=candidates[0]['payload'];self._factory(environment([payload]))
                selected.append(payload)
            except (ValueError,pg.AdmissionDenied,KeyError,TypeError) as exc:errors[provider]=f'{type(exc).__name__}: {exc}'
        self.route_errors=errors
        if not selected:raise ValueError('No ready reviewed route: '+'; '.join(k+': '+v for k,v in errors.items()))
        return environment(selected)
    @property
    def models(self):
        try:return self._factory(self.available_environment()).models
        except ValueError:return {}
    @property
    def verify_vendor(self):
        return self._factory(self.available_environment()).verify_vendor
    def for_task(self,task_id,selection=None):
        pin=self.store.task_model_pin(task_id)
        if pin is None:raise KeyError(task_id)
        name='model-pins:'+str(pin['generation']);old=pin['environment']
        if old is None:
            if selection is None:chosen=self.available_environment()
            else:
                from .model_registry import resolve
                chosen=environment([resolve(self.registry(),*selection)['payload']])
            self.store.retain_decision_review(task_id,name,'model-pins-v1',chosen)
            old=self.store.decision_review(task_id,name,'model-pins-v1')
        factory=self._factory(old)
        if selection is not None and factory.models.get(selection[0])!=selection[1]:raise ValueError('Task retains a different model pin; no automatic replacement')
        return factory
    def __call__(self,capability,job_id):
        job=self.store.worker_job(job_id)
        if not job:raise KeyError(job_id)
        return self.for_task(job['task_id'])(capability,job_id)
    def accepts(self,provider,model):
        # Existing immutable factories are accessible only through task-owned job resolution.
        return any(factory.models.get(provider)==model for factory in self._factories.values())
    def refresh_launch(self):
        import warnings
        self._launch_errors={}
        for row in PinRepository(self.store._postgres).list():
            provider=row['provider']
            if provider=='ollama' or not row['is_default']:continue
            try:
                if not (self.environ.get(pg.KEY_ENV) or '').strip():raise ValueError('DeepSeek key is not configured; set it only to enable paid work')
                result=refresh(row['payload'])
                if result.get('warning'):warnings.warn(result['warning'])
                elif result['changed']:
                    before=row['payload']['row']['provider_snapshot'];after=result['payload']['row']['provider_snapshot']
                    if any(before.get(k)!=after.get(k) for k in vs.FACT_KEYS):raise ValueError('Vendor terms changed. Review and approve Back office > Model pins before new paid work.')
                    vs.verify_current_sources(before,cached=False)
            except Exception as exc:
                self._launch_errors[provider]=f'{type(exc).__name__}: {exc}'
                warnings.warn(provider+' route unavailable: '+self._launch_errors[provider])
        self.available_environment()
        for provider,reason in self.route_errors.items():warnings.warn(provider+' route unavailable: '+reason)
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


def pin_verifier(payload):
    """Unchanged successful checks renew in memory, never silently adopt changed terms."""
    import time,warnings
    last=[None,None]
    def verify():
        now=datetime.datetime.now(datetime.timezone.utc)
        if last[0] is not None and time.monotonic()-last[0]<vs.check_max_age_minutes()*60:return True
        candidate=copy.deepcopy(payload)
        if last[1]:candidate['row']['provider_snapshot']['fetched_at']=last[1]
        result=refresh(candidate)
        if result['warning']:
            warnings.warn(result['warning']);return True
        before=payload['row']['provider_snapshot'];after=result['payload']['row']['provider_snapshot']
        if any(before.get(k)!=after.get(k) for k in vs.FACT_KEYS):raise ValueError('Vendor terms changed; task retains its reviewed pin and new paid dispatch is blocked. Open Model pins to review.')
        last[:]=[time.monotonic(),now.isoformat()]
        return True
    return verify
