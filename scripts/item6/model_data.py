"""Vendor documentation to a private non-executable proposal, never a paid call.

HTML is not a pricing API. Only the documented current table contract is accepted;
source drift, missing units, duplicate rows and redirects all fail closed.
"""
from decimal import Decimal
from html.parser import HTMLParser
from pathlib import Path
import datetime,hashlib,json,os,re,urllib.request

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
    with urllib.request.build_opener(NoRedirect).open(urllib.request.Request(url,headers={'User-Agent':'item6-public-docs/1'}),timeout=20) as r:
        if r.status!=200 or 'text/html' not in r.headers.get('Content-Type',''):raise SourceChanged('HTML source required')
        body=r.read(MAX_BYTES+1)
        if len(body)>MAX_BYTES:raise SourceChanged('source too large')
        return body.decode('utf-8')


def propose(directory,model,fetcher=fetch):
    from isolation import RunIsolation
    directory=Path(directory).absolute()
    if any(p.is_symlink() for p in (directory,*directory.parents)) or not directory.is_dir() or directory.stat().st_mode&0o077:raise ValueError('existing private non-symlink scaffold required')
    private=directory/'private';run=private/'run.json';evidence=directory/'evidence'
    if private.is_symlink() or private.stat().st_mode&0o077:raise ValueError('private config required')
    if evidence.is_symlink() or evidence.stat().st_mode&0o077 or any(evidence.iterdir()):raise ValueError('empty fresh evidence required')
    if run.exists():
        if run.is_symlink() or run.stat().st_mode&0o077:raise ValueError('private config required')
        config=json.loads(run.read_text())
    else:
        # No .env or credential contents needed for fixed public-doc fetching.
        from single_config import root_check
        root_check(directory)
        config={'model':model}
    if config['model'] not in ('REPLACE_LOCALLY',model):raise ValueError('existing different model must not be replaced')
    target=private/'deepseek-proposal.json'
    if target.exists() or target.is_symlink():raise ValueError('proposal already exists; review it instead of overwriting')
    sources={url:fetcher(url) for url in (PRICING_URL,API_URL)}
    result=parse(sources[PRICING_URL],sources[API_URL],model)
    result['fetched_at']=datetime.datetime.now(datetime.timezone.utc).isoformat()
    result['sources']=[{'url':url,'sha256':hashlib.sha256(body.encode()).hexdigest()} for url,body in sources.items()]
    result['run_model_proposal']={'model':model}
    RunIsolation.write(target,result)
    # Return public values and narrow status, not private run configuration.
    return {'proposal_file':str(target),'reviewed':False,'model':model,'model_version':result['model_version'],
        'published_usd_per_million':result['published_usd_per_million'],'published_period_rule':result['published_period_rule'],'raw_context_limit':result['raw_context_limit'],
        'exact_maximum_output_tokens':result['context_proposal']['maximum_output_tokens'],'sources':result['sources'],
        'execution_config_changed':False,'run_started':False,'manual_review_required':result['manual_review_required']}
