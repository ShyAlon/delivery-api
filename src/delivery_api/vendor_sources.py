"""Vendor documentation parser and pre-call source check. Public reads only, never a paid call.

HTML is not a pricing API. Only the documented current table contract is accepted;
source drift, missing units, duplicate rows and redirects all fail closed.
"""
from decimal import Decimal
from html.parser import HTMLParser
import datetime,hashlib,re,time,urllib.error,urllib.request
from .provider_snapshot import ProviderSnapshot

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
    api_models=re.search(r'model string required Possible values: \[ ([^]]+) \] ID of the model to use',text)
    if not api_models or {v.strip() for v in api_models[1].split(',')}!=set(models):raise SourceChanged('API model identities disagree')
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


GRACE_MINUTES=60
# A page that no longer parses only warns when the problem is layout; anything about rates, periods, limits or conversion still stops.
HARD_PARSE_WORDS=('rate','period','limit','output','conversion','USD','selected model')
WARN_ONLY_FACTS=('model_version',)   # facts that never change what a call costs; every other pinned fact is money or a limit
_WARNED={}


def _note(log,kind,text):
    """One line with an ISO time,."""
    line=datetime.datetime.now(datetime.timezone.utc).isoformat()+' WARNING '+text
    log(line)


def verify_current_sources(snapshot,fetcher=fetch,*,cached=True,clock=time.monotonic,log=None):
    """Before a new contact/settlement; public reads, never provider inference.

    A good check is reused for check_max_age_minutes(). Within GRACE_MINUTES of the last good check
    (this process, or the read that pinned the snapshot) a page that cannot be read, no longer parses,
    or differs only in a fact that does not change cost (model_version) is a logged warning and a
    finding, and the run continues. A changed price, peak rule or limit always stops. After the grace
    period the old rule applies: any unreadable or changed source stops. A failed check is never cached."""
    now=datetime.datetime.now(datetime.timezone.utc)
    checked=ProviderSnapshot(snapshot);checked.fresh(now)
    sources={s['url']:s['sha256'] for s in snapshot['sources']}
    if set(sources)!={PRICING_URL,API_URL}:raise ValueError('fixed vendor source set changed; admission stopped')
    log=log or (lambda text:print(text,flush=True))
    key=checked.id;last=_GOOD.get(key)
    if cached and last and clock()-last[0]<check_max_age_minutes()*60:return True
    if cached and key in _WARNED and clock()-_WARNED[key]<check_max_age_minutes()*60:return True
    _GOOD.pop(key,None)
    ages=[(now-checked.fetched).total_seconds()/60]
    if last:ages.append((clock()-last[0])/60)
    grace=min(ages)<GRACE_MINUTES
    last_text='last good check '+last[1] if last else 'no earlier good check in this process'
    def warn(kind,why):
        _WARNED[key]=clock()
        _note(log,kind,'vendor check: '+why+'; last good check %.0f minutes ago (grace %d), so the run continues; '%(min(ages),GRACE_MINUTES)+last_text)
        return True
    try:bodies={url:fetch_verified_source(url,fetcher) for url in sources}
    except Exception as exc:
        if not grace:raise
        return warn('vendor-read',type(exc).__name__+': '+str(exc)[:200].replace('\n',' '))
    if any(hashlib.sha256(bodies[u].encode()).hexdigest()!=d for u,d in sources.items()):
        try:current=parse(bodies[PRICING_URL],bodies[API_URL],snapshot['model'])
        except ValueError as exc:
            if grace and not any(w in str(exc) for w in HARD_PARSE_WORDS):return warn('vendor-read','the published model table no longer parses ('+str(exc)[:200]+')')
            raise ValueError('vendor source changed during run and the published model table no longer parses ('+str(exc)+'); preserve accepted attempts and review new snapshot; '+last_text) from None
        changed=[k for k in FACT_KEYS if current.get(k)!=snapshot.get(k)]
        if current['context_proposal']['maximum_output_tokens']!=snapshot['context_proposal']['maximum_output_tokens']:changed.append('maximum_output_tokens')
        hard=[k for k in changed if k not in WARN_ONLY_FACTS]
        if hard or (changed and not grace):raise ValueError('vendor source changed during run: pinned terms differ ('+', '.join(changed)+'); preserve accepted attempts and review new snapshot; '+last_text)
        if changed:
            return warn('vendor-change','pinned '+', '.join('%s %r -> %r'%(k,snapshot.get(k),current.get(k)) for k in changed)+'; prices, peak rule and limits unchanged')
        log(datetime.datetime.now(datetime.timezone.utc).isoformat()+' vendor pages changed but every pinned term is identical; check passes')
    _GOOD[key]=(clock(),datetime.datetime.now(datetime.timezone.utc).isoformat())
    return True

