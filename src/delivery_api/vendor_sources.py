"""Vendor documentation parser and pre-call source check. Public reads only, never a paid call.

HTML is not a pricing API. Only the documented current table contract is accepted;
source drift, missing units, duplicate rows and redirects all fail closed.
"""
from decimal import Decimal
from html.parser import HTMLParser
import datetime,hashlib,re,time,urllib.error,urllib.request
from . import vendor_status
from .provider_snapshot import ProviderSnapshot
from .attempt_ledger import AdmissionDenied

PRICING_URL='https://api-docs.deepseek.com/quick_start/pricing/'
API_URL='https://api-docs.deepseek.com/api/create-chat-completion/'
MAX_BYTES=1024*1024


class SourceChanged(ValueError):pass


class Table(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True);self.tables=[];self.rows=None;self.row=None;self.cell=None;self.article=False;self.article_text=[]
    def handle_starttag(self,tag,attrs):
        attrs=dict(attrs)
        if tag=='article':self.article=True
        if tag=='table':
            if self.rows is not None:raise SourceChanged('nested table')
            self.rows=[]
        if tag=='tr' and self.rows is not None:self.row=[]
        if tag in ('th','td') and self.row is not None:
            try:r=int(attrs.get('rowspan','1'));c=int(attrs.get('colspan','1'))
            except ValueError:raise SourceChanged('invalid span') from None
            if not 1<=r<=10 or not 1<=c<=5:raise SourceChanged('span changed')
            self.cell={'text':[],'r':r,'c':c}
    def handle_data(self,text):
        if self.cell is not None:self.cell['text'].append(text)
        if self.article:self.article_text.append(text)
    def handle_endtag(self,tag):
        if tag=='article':self.article=False
        if tag in ('th','td') and self.cell is not None:
            self.row.append((' '.join(' '.join(self.cell['text']).split()),self.cell['r'],self.cell['c']));self.cell=None
        if tag=='tr' and self.row is not None:self.rows.append(self.row);self.row=None
        if tag=='table' and self.rows is not None:self.tables.append(self.rows);self.rows=None


def expanded(rows):
    grid={};out=[]
    for y,row in enumerate(rows):
        x=0
        for text,height,width in row:
            while (y,x) in grid:x+=1
            for dy in range(height):
                for dx in range(width):
                    if (y+dy,x+dx) in grid:raise SourceChanged('overlapping table spans')
                    grid[y+dy,x+dx]=text
            x+=width
        if any((y,x) not in grid for x in range(5)) or any((y,x) in grid for x in range(5,15)):raise SourceChanged('table width changed')
        out.append([grid[y,x] for x in range(5)])
    return out


def parse(pricing_html,api_html,model):
    p=Table();p.feed(pricing_html);a=Table();a.feed(api_html)
    # The vendor may add unrelated tables (e.g. Search & Fetch pricing); only the model table matters.
    model_tables=[t for t in p.tables if t and t[0] and t[0][0][0]=='MODEL']
    if len(model_tables)!=1:raise SourceChanged('expected one explicit model/pricing table')
    rows=expanded(model_tables[0]);header=rows[0]
    models=[re.sub(r'\s*\(\d+\)$','',v) for v in header[3:]]
    if header[:3]!=['MODEL']*3 or len(set(models))!=2 or any(not re.fullmatch('deepseek-[a-z0-9-]{1,60}',m) for m in models):raise SourceChanged('model table changed')
    if model not in models:raise SourceChanged('selected model not in current published table')
    col=models.index(model)+3
    def one(label):
        values=[r[col] for r in rows if r[0]==label]
        if len(values)!=1 or not values[0]:raise SourceChanged('missing or duplicate source field')
        return values[0]
    version=one('MODEL VERSION');context=one('CONTEXT LENGTH');output=one('MAX OUTPUT')
    if not re.fullmatch(r'\d+(?:\.\d+)?[KM]',context) or not re.fullmatch(r'MAXIMUM: \d+[KM]',output):raise SourceChanged('limit unit shape changed')
    text=' '.join(' '.join(a.article_text).split())
    match=re.search(r'The value must be between 1 and (\d+)K \((\d+)\)',text)
    if not match or output!='MAXIMUM: '+match[1]+'K':raise SourceChanged('output sources disagree or exact integer missing')
    # The OpenAPI renderer used brackets around enum values until October 30.
    # Match either complete presentation within the model field only. Keep
    # exact identities, cardinality and paired brackets fail-closed.
    enum_values=r'deepseek-[a-z0-9-]{1,60}(?: , deepseek-[a-z0-9-]{1,60})*'
    api_models=re.findall(r'model string required Possible values: (\[ '+enum_values+r' \]|'+enum_values+r') ID of the model to use',text)
    if len(api_models)!=1:raise SourceChanged('API model identities disagree')
    identities=api_models[0]
    if identities.startswith('[ '):identities=identities[2:-2]
    identities=identities.split(' , ')
    if len(identities)!=len(models) or set(identities)!=set(models):raise SourceChanged('API model identities disagree')
    if not re.search(r'prices (?:listed below are in|are listed in) units of per 1M tokens',' '.join(' '.join(p.article_text).split())):raise SourceChanged('explicit per-million units required')
    rates={}
    labels={'1M INPUT TOKENS (CACHE HIT)':'cache_hit','1M INPUT TOKENS (CACHE MISS)':'cache_miss','1M OUTPUT TOKENS':'output'}
    for label,key in labels.items():
        entries=[r for r in rows if r[1]==label]
        if len(entries)!=2 or {r[2] for r in entries}!={'PEAK','OFF-PEAK'}:raise SourceChanged('explicit two-period rates required')
        for r in entries:
            if not re.fullmatch(r'\$\d+(?:\.\d{1,6})?',r[col]):raise SourceChanged('USD price unit changed')
            value=Decimal(r[col][1:]);period=r[2].lower().replace('-','_')
            rates.setdefault(period,{})[key]=str(value)
    for key in ('cache_hit','cache_miss','output'):
        if Decimal(rates['peak'][key])!=2*Decimal(rates['off_peak'][key]):raise SourceChanged('stated period ratio disagrees with rates')
    # Explicit large unit scale avoids rounding $0.003 / 1M tokens to zero.
    scale=10**12
    catalogs={}
    for period,values in rates.items():
        fixed={k:int(Decimal(v)*scale/1000000) for k,v in values.items()}
        if any(Decimal(values[k])*scale/1000000!=v for k,v in fixed.items()):raise SourceChanged('nonintegral conversion')
        catalogs[period]=[{'provider':'deepseek','model':model,'price_version':'REPLACE_LOCALLY_AFTER_REVIEW',
            'source':PRICING_URL,'expense_input':fixed['cache_miss'],'expense_cache_hit':fixed['cache_hit'],
            'expense_cache_miss':fixed['cache_miss'],'expense_output':fixed['output'],'expense_units_per_usd':scale,
            'retail_input':None,'retail_output':None,'retail_units_per_credit':1000,
            'usage_categories':['input_tokens','output_tokens','cache_hit_tokens','cache_miss_tokens']}]
    if 'Off-peak rates are half of the peak rates.' not in ' '.join(' '.join(p.article_text).split()):raise SourceChanged('period rule changed')
    pricing_text=' '.join(' '.join(p.article_text).split())
    period_match=re.search(r'Peak hours are [^.]{1,250}\. All other hours are off-peak[^.]{0,150}\.',pricing_text)
    if not period_match:raise SourceChanged('explicit current pricing period rule missing')
    return {'published_period_rule':period_match[0],'model':model,'model_version':version,'published_usd_per_million':rates,
        'catalog_proposals':catalogs,'context_proposal':{'model':model,'context_tokens':None,'maximum_output_tokens':int(match[2]),
            'source':PRICING_URL+' ; '+API_URL,'bound_version':'REPLACE_LOCALLY_AFTER_REVIEW'},
        'raw_context_limit':context,'raw_output_limit':output,'reviewed':False,
        'manual_review_required':['numeric context limit: vendor publishes a shorthand, not an exact integer',
            'choose applicable peak/off-peak rates; fixed run catalog must not cross pricing periods',
            'retail_input and retail_output: owner margin decision, never fetched',
            'vendor source/version review before copying into executable config'],
        'execution_config_changed':False,'run_started':False}


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):raise SourceChanged('source redirect forbidden')


def fetch(url):
    if url not in (PRICING_URL,API_URL):raise SourceChanged('fixed public documentation sources only')
    with urllib.request.build_opener(NoRedirect).open(urllib.request.Request(url,headers={'User-Agent':'requi-public-docs/1'}),timeout=20) as r:
        if r.status!=200 or 'text/html' not in r.headers.get('Content-Type',''):raise SourceChanged('HTML source required')
        body=r.read(MAX_BYTES+1)
        if len(body)>MAX_BYTES:raise SourceChanged('source too large')
        return body.decode('utf-8')


def fetch_verified_source(url,fetcher=fetch,sleeper=time.sleep):
    # Public GET only. Never retry model dispatch or mismatched source content.
    for attempt in range(3):
        try:return fetcher(url)
        except urllib.error.HTTPError as exc:
            if exc.code not in (429,500,502,503,504) or attempt==2:raise
        except (urllib.error.URLError,TimeoutError,ConnectionError):
            if attempt==2:raise
        sleeper((1,2)[attempt])


_GOOD={}
FACT_KEYS=('model','model_version','published_usd_per_million','published_period_rule','raw_context_limit','raw_output_limit')


def check_max_age_minutes():
    """One setting, one place: VENDOR_CHECK_MAX_AGE_MINUTES (default 60, 1 to 60)."""
    import os
    raw=os.environ.get('VENDOR_CHECK_MAX_AGE_MINUTES','60')
    try:minutes=int(raw)
    except ValueError:raise ValueError('VENDOR_CHECK_MAX_AGE_MINUTES must be a whole number of minutes from 1 to 60') from None
    if not 1<=minutes<=60:raise ValueError('VENDOR_CHECK_MAX_AGE_MINUTES must be a whole number of minutes from 1 to 60')
    return minutes


# Failed refresh can use verified stored prices for strictly less than 24 hours.
_WARNED={}


def verify_current_sources(snapshot,fetcher=fetch,*,cached=True,clock=time.monotonic,log=None):
    """Before a new contact/settlement; public reads, never provider inference.

    A good check is reused for check_max_age_minutes(). Failed refreshes warn only
    while the last verified prices are under 24 hours old. No failed check renews them."""
    now=datetime.datetime.now(datetime.timezone.utc)
    checked=ProviderSnapshot(snapshot);checked.fresh(now)
    sources={s['url']:s['sha256'] for s in snapshot['sources']}
    if set(sources)!={PRICING_URL,API_URL}:raise ValueError('fixed vendor source set changed; admission stopped')
    log=log or (lambda text:print(text,flush=True))
    key=checked.id;last=_GOOD.get(key)
    if cached and last and clock()-last[0]<check_max_age_minutes()*60:return True
    verified_at=datetime.datetime.fromisoformat(last[1]) if last else checked.fetched
    age=now-verified_at
    if cached and key in _WARNED and age<datetime.timedelta(hours=24) and clock()-_WARNED[key]<check_max_age_minutes()*60:return True
    ages=[(now-checked.fetched).total_seconds()/60]
    if last:ages.append((clock()-last[0])/60)
    last_text='last good check '+last[1] if last else 'no earlier good check in this process'
    def warn(kind,why):
        if age>=datetime.timedelta(hours=24):
            raise AdmissionDenied('vendor prices could not be refreshed and stored prices are at least 24 hours old: '+why)
        _WARNED[key]=clock()
        vendor_status.warn(kind,'vendor check: '+why+'; last good check %.0f minutes ago, so the run continues; '%min(ages)+last_text,log=log)
        return True
    try:bodies={url:fetch_verified_source(url,fetcher) for url in sources}
    except Exception as exc:
        return warn('vendor-read',type(exc).__name__+': '+str(exc)[:200].replace('\n',' '))
    if any(hashlib.sha256(bodies[u].encode()).hexdigest()!=d for u,d in sources.items()):
        try:current=parse(bodies[PRICING_URL],bodies[API_URL],snapshot['model'])
        except ValueError as exc:
            return warn('vendor-read','the published model table no longer parses ('+str(exc)[:200]+'); prices or limits may have changed, calls and billing continue on the PINNED terms')
        changed=[k for k in FACT_KEYS if current.get(k)!=snapshot.get(k)]
        if current['context_proposal']['maximum_output_tokens']!=snapshot['context_proposal']['maximum_output_tokens']:changed.append('maximum_output_tokens')
        if changed:
            now_value=lambda k:current['context_proposal']['maximum_output_tokens'] if k=='maximum_output_tokens' else current.get(k)
            was_value=lambda k:snapshot['context_proposal']['maximum_output_tokens'] if k=='maximum_output_tokens' else snapshot.get(k)
            return warn('vendor-change','pinned terms changed: '+'; '.join('%s was %r, now %r'%(k,was_value(k),now_value(k)) for k in changed)+'; calls and billing continue on the PINNED terms until the engineers update them')
        log(datetime.datetime.now(datetime.timezone.utc).isoformat()+' vendor pages changed but every pinned term is identical; check passes')
    _GOOD[key]=(clock(),datetime.datetime.now(datetime.timezone.utc).isoformat())
    vendor_status.verified(snapshot)
    return True

