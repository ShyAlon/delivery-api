"""Public vendor price cache. Failed refreshes never advance the fetched timestamp."""
import datetime,json,os,tempfile
from pathlib import Path
from .provider_snapshot import ProviderSnapshot
from . import production_gateway as pg


def load_cached_pin(path,model,context,maximum_output,now=None):
    data=json.loads(Path(path).read_text())
    text=data['env']
    env=dict(line.split('=',1) for line in text.splitlines() if line and not line.startswith('#'))
    if set(env)!={pg.PRICES_ENV,pg.CEILING_ENV}:raise ValueError('unexpected cached settings')
    rows=json.loads(env[pg.PRICES_ENV]);bound=json.loads(env[pg.CEILING_ENV])
    if len(rows)!=1 or rows[0]['provider']!='deepseek' or rows[0]['model']!=model:raise ValueError('cached model mismatch')
    snap=ProviderSnapshot(rows[0]['provider_snapshot'])
    now=now or datetime.datetime.now(datetime.timezone.utc);snap.fresh(now)
    if data['fetched_at']!=snap.data['fetched_at']:raise ValueError('cached timestamp mismatch')
    age=(now-snap.fetched).total_seconds()/3600
    if age>=24:raise ValueError('stored price is at least 24 hours old')
    if bound['model']!=model or bound['context_tokens']!=context or bound['maximum_output_tokens']!=maximum_output:raise ValueError('cached bounds mismatch')
    return text,age


def save_cached_pin(path,text,fetched_at):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    fd,tmp=tempfile.mkstemp(prefix='.price-',dir=path.parent)
    try:
        with os.fdopen(fd,'w') as stream:json.dump({'env':text,'fetched_at':fetched_at},stream)
        os.replace(tmp,path)
    finally:
        if os.path.exists(tmp):os.unlink(tmp)
