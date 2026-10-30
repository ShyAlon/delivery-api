"""PG resource-metrics repository for the PG application runtime.

Selected by default for PG applications, with a distinct host source identity.
Schema/role rollout and activation remain explicit; no history import. Uses the shared process pool, no SQLite fallback,
DDL or provider calls. The process role is restricted to metrics tables, not
an ambient request tenant. Read APIs require the existing operator logs.read
registry policy; none are exposed by this writer.
"""
from contextlib import contextmanager
import math

class PostgresMetrics:
    def __init__(self,dsn,*,source_id):
        if not isinstance(dsn,str) or not dsn.startswith(('postgres://','postgresql://','host=')):
            raise ValueError('explicit PostgreSQL DSN required')
        if not isinstance(source_id,str) or not 1<=len(source_id)<=200 or any(ord(c)<32 for c in source_id):
            raise ValueError('stable bounded process source identity required')
        self.dsn=dsn;self.source_id=source_id

    @contextmanager
    def transaction(self):
        from .db_pool import pool_for
        with pool_for(self.dsn).connection(timeout=0.25) as c:
            with c.transaction():
                c.execute('SET LOCAL ROLE requi_metrics_writer')
                c.execute("SET LOCAL statement_timeout='1000ms'")
                c.execute("SET LOCAL lock_timeout='100ms'")
                c.execute("SELECT set_config('requi.metrics_source',%s,true)",(self.source_id,))
                # Serializes append/rollup per source, never across hosts. Prevents
                # late raw rows racing the summary/delete transaction.
                c.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',('resource-metrics:'+self.source_id,))
                yield c

    @staticmethod
    def validate(row):
        for field,value in row.items():
            if field in ('run_key','task_id','session_id','phase'):
                if value is None and field in ('session_id','phase'):continue
                if not isinstance(value,str) or not 1<=len(value)<=240 or any(ord(c)<32 for c in value):
                    raise ValueError('bounded metrics identity required')
            elif field in ('pid','procs'):
                if value is not None and (type(value) is not int or value<0 or value>2147483647):
                    raise ValueError('bounded metrics integer required')
            elif value is None and field!='ts':continue
            elif type(value) not in (int,float) or not math.isfinite(value) or value<0:
                raise ValueError('finite nonnegative metrics value required')

    def append_run(self,**row):
        fields=('ts','run_key','task_id','session_id','phase','pid','procs','rss_mb','peak_rss_mb','cpu_seconds','cpu_pct')
        if set(row)!=set(fields):raise ValueError('exact run sample required')
        self.validate(row)
        if row['ts']>253402300799:raise ValueError('bounded metrics timestamp required')
        with self.transaction() as c:
            if c.execute('SELECT 1 FROM public.resource_run_summaries WHERE run_key=%s',(row['run_key'],)).fetchone():
                raise ValueError('run already rolled up; late sample requires explicit reconciliation')
            c.execute('INSERT INTO public.resource_run_samples(source_id,'+','.join(fields)+') VALUES ('+','.join(['%s']*12)+')',(self.source_id,*(row[f] for f in fields)))

    def append_container(self,**row):
        fields=('ts','mem_used_mb','mem_limit_mb','cpu_seconds','cpu_pct')
        if set(row)!=set(fields):raise ValueError('exact container sample required')
        self.validate(row)
        if row['ts']>253402300799:raise ValueError('bounded metrics timestamp required')
        with self.transaction() as c:
            if c.execute("SELECT 1 FROM public.resource_container_summaries WHERE day=to_char(to_timestamp(%s) AT TIME ZONE 'UTC','YYYY-MM-DD')",(row['ts'],)).fetchone():
                raise ValueError('day already rolled up; late sample requires explicit reconciliation')
            c.execute('INSERT INTO public.resource_container_samples(source_id,'+','.join(fields)+') VALUES ('+','.join(['%s']*6)+')',(self.source_id,*(row[f] for f in fields)))

    def maintain(self,*,cutoff,now):
        """Metrics-only bounded cross-source cleanup, including retired hosts."""
        from .db_pool import pool_for
        with pool_for(self.dsn).connection(timeout=0.25) as c:
            with c.transaction():
                c.execute('SET LOCAL ROLE requi_metrics_writer')
                c.execute("SET LOCAL statement_timeout='1000ms'")
                c.execute("SET LOCAL lock_timeout='100ms'")
                row=c.execute('SELECT * FROM public.requi_maintain_resource_metrics(%s,%s)',(cutoff,now)).fetchone()
                return row['runs'],row['days']

    def rollup(self,*,cutoff,now):
        if any(type(v) not in (int,float) or not math.isfinite(v) for v in (cutoff,now)):
            raise ValueError('finite retention timestamps required')
        with self.transaction() as c:
            # Identity must be stable within a run. Reject ambiguous rows rather
            # than arbitrarily choosing task/phase identity from SQLite GROUP BY.
            runs=c.execute('''SELECT run_key,min(task_id) task_id,min(session_id) session_id,min(phase) phase,min(pid) pid,
                min(ts) a,max(ts) b,count(*) n,avg(cpu_pct) ac,max(cpu_pct) mc,
                avg(rss_mb) ar,max(rss_mb) mr,max(cpu_seconds) cs,
                count(DISTINCT (task_id,session_id,phase,pid)) identities
                FROM public.resource_run_samples GROUP BY run_key HAVING max(ts)<%s''',(cutoff,)).fetchall()
            for r in runs:
                if r['identities']!=1:raise ValueError('ambiguous run metrics identity')
                c.execute('''INSERT INTO public.resource_run_summaries
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT(source_id,run_key) DO UPDATE SET
                    task_id=excluded.task_id,session_id=excluded.session_id,phase=excluded.phase,pid=excluded.pid,
                    started_at=excluded.started_at,ended_at=excluded.ended_at,samples=excluded.samples,
                    avg_cpu_pct=excluded.avg_cpu_pct,max_cpu_pct=excluded.max_cpu_pct,
                    avg_rss_mb=excluded.avg_rss_mb,max_rss_mb=excluded.max_rss_mb,
                    cpu_seconds=excluded.cpu_seconds,rolled_up_at=excluded.rolled_up_at''',
                    (self.source_id,r['run_key'],r['task_id'],r['session_id'],r['phase'],r['pid'],r['a'],r['b'],r['n'],_round(r['ac']),_round(r['mc']),_round(r['ar']),_round(r['mr']),r['cs'],now))
                c.execute('DELETE FROM public.resource_run_samples WHERE run_key=%s',(r['run_key'],))
            days=c.execute('''SELECT to_char(to_timestamp(ts) AT TIME ZONE 'UTC','YYYY-MM-DD') d,
                count(*) n,avg(mem_used_mb) am,max(mem_used_mb) mm,avg(cpu_pct) ac,max(cpu_pct) mc
                FROM public.resource_container_samples GROUP BY d HAVING max(ts)<%s''',(cutoff,)).fetchall()
            for d in days:
                c.execute('''INSERT INTO public.resource_container_summaries VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT(source_id,day) DO UPDATE SET samples=excluded.samples,
                    avg_mem_mb=excluded.avg_mem_mb,max_mem_mb=excluded.max_mem_mb,
                    avg_cpu_pct=excluded.avg_cpu_pct,max_cpu_pct=excluded.max_cpu_pct,rolled_up_at=excluded.rolled_up_at''',
                    (self.source_id,d['d'],d['n'],_round(d['am']),_round(d['mm']),_round(d['ac']),_round(d['mc']),now))
                c.execute("DELETE FROM public.resource_container_samples WHERE to_char(to_timestamp(ts) AT TIME ZONE 'UTC','YYYY-MM-DD')=%s AND ts<%s",(d['d'],cutoff))
            return len(runs),len(days)

    def for_task(self,task_id,*,limit=2000,problem=None):
        if type(limit) is not int or not 1<=limit<=2000:raise ValueError('bounded metrics page required')
        with self.transaction() as c:
            samples=c.execute('SELECT ts,run_key,session_id,phase,pid,procs,rss_mb,peak_rss_mb,cpu_seconds,cpu_pct FROM resource_run_samples WHERE task_id=%s ORDER BY ts LIMIT %s',(task_id,limit)).fetchall()
            summaries=c.execute('SELECT * FROM resource_run_summaries WHERE task_id=%s ORDER BY started_at',(task_id,)).fetchall()
            return dict(task_id=task_id,problem=problem,samples=samples,summaries=summaries)

    def overview(self,*,limit=8,problem=None,raw_days=3,interval_s=10):
        if type(limit) is not int or not 1<=limit<=100:raise ValueError('bounded metrics page required')
        with self.transaction() as c:
            live=c.execute('''SELECT run_key,min(task_id) task_id,min(session_id) session_id,min(phase) phase,
                min(ts) started_at,max(ts) ended_at,count(*) samples,avg(cpu_pct) avg_cpu_pct,
                max(cpu_pct) max_cpu_pct,avg(rss_mb) avg_rss_mb,max(rss_mb) max_rss_mb,max(cpu_seconds) cpu_seconds
                FROM resource_run_samples GROUP BY run_key ORDER BY max(ts) DESC LIMIT %s''',(limit,)).fetchall()
            for r in live:
                r['summary']=False
                values=c.execute('SELECT rss_mb FROM resource_run_samples WHERE run_key=%s ORDER BY ts',(r['run_key'],)).fetchall()
                values=[v['rss_mb'] for v in values]
                if len(values)>12:values=[values[int(i*(len(values)-1)/11)] for i in range(12)]
                r['timeline_mb']=[round(v) for v in values if v is not None]
            kept=c.execute('SELECT * FROM resource_run_summaries ORDER BY ended_at DESC LIMIT %s',(limit,)).fetchall()
            for r in kept:r['summary']=True;r['timeline_mb']=[]
            runs=sorted(live+kept,key=lambda r:r['ended_at'] or 0,reverse=True)[:limit]
            last=c.execute('SELECT ts,mem_used_mb,mem_limit_mb,cpu_pct FROM resource_container_samples ORDER BY ts DESC LIMIT 1').fetchone()
            container=None
            if last:
                day=c.execute('SELECT count(*) n,avg(mem_used_mb) am,max(mem_used_mb) mm,avg(cpu_pct) ac,max(cpu_pct) mc FROM resource_container_samples WHERE ts>=%s',(last['ts']-86400,)).fetchone()
                container=dict(last=last,day=day)
            return dict(path='postgres:resource_metrics',problem=problem,runs=runs,container=container,raw_days=raw_days,interval_s=interval_s)


def _round(value):return None if value is None else round(value,1)


def read_operator(reader,owner_store,*,task_id=None,limit=100):
    """Bounded registry read, never merge host identities into one series."""
    if type(limit) is not int or not 1<=limit<=(2000 if task_id is not None else 100):raise ValueError('bounded metrics page required')
    if task_id is not None and (not isinstance(task_id,str) or not 1<=len(task_id)<=240):raise ValueError('bounded task identity required')
    with reader._read(owner_store,'logs.read',repeatable_read=True) as (c,authority):
        c.execute("SET LOCAL statement_timeout='1500ms'")
        if task_id is not None:
            rows=c.execute('SELECT * FROM resource_run_samples WHERE task_id=%s ORDER BY ts DESC,id DESC LIMIT %s',(task_id,limit+1)).fetchall()
            summaries=c.execute('SELECT * FROM resource_run_summaries WHERE task_id=%s ORDER BY ended_at DESC LIMIT %s',(task_id,limit+1)).fetchall()
            return {'task_id':task_id,'samples':list(reversed(rows[:limit])),'summaries':summaries[:limit],
                    'truncated':len(rows)>limit or len(summaries)>limit,'problem':None,'scope':'reviewed_cross_user_operator_read','registry_version':authority['registry_version']}
        live=c.execute('''SELECT source_id,run_key,min(task_id) task_id,min(session_id) session_id,min(phase) phase,
            min(ts) started_at,max(ts) ended_at,count(*) samples,avg(cpu_pct) avg_cpu_pct,max(cpu_pct) max_cpu_pct,
            avg(rss_mb) avg_rss_mb,max(peak_rss_mb) max_rss_mb,max(cpu_seconds) cpu_seconds
            FROM resource_run_samples GROUP BY source_id,run_key ORDER BY max(ts) DESC LIMIT %s''',(limit+1,)).fetchall()
        kept=c.execute('SELECT * FROM resource_run_summaries ORDER BY ended_at DESC LIMIT %s',(limit+1,)).fetchall()
        for row in live:
            row['summary']=False
            # SQL ranks all retained points but transfers at most twelve per
            # series, preserving the legacy shape without an unbounded fetch.
            points=c.execute('''SELECT rss_mb FROM (
                SELECT rss_mb,row_number() OVER(ORDER BY ts,id) n,count(*) OVER() total
                FROM resource_run_samples WHERE source_id=%s AND run_key=%s) ranked
                WHERE total<=12 OR n IN (SELECT 1+floor(i*(total-1)/11.0)::bigint FROM generate_series(0,11) i)
                ORDER BY n''',(row['source_id'],row['run_key'])).fetchall()
            row['timeline_mb']=[round(v['rss_mb']) for v in points if v['rss_mb'] is not None]
        for row in kept:row['summary']=True;row['timeline_mb']=[]
        containers=c.execute('''SELECT DISTINCT ON (source_id) source_id,ts,mem_used_mb,mem_limit_mb,cpu_pct
            FROM resource_container_samples ORDER BY source_id,ts DESC LIMIT %s''',(limit+1,)).fetchall()
        for box in containers:
            box['day']=c.execute('''SELECT count(*) n,avg(mem_used_mb) am,max(mem_used_mb) mm,avg(cpu_pct) ac,max(cpu_pct) mc
                FROM resource_container_samples WHERE source_id=%s AND ts>=%s AND ts<=%s''',
                (box['source_id'],box['ts']-86400,box['ts'])).fetchone()
        runs=sorted(live+kept,key=lambda r:r['ended_at'] or 0,reverse=True)
    from . import config
    return {'path':'postgres:resource_metrics','problem':None,'runs':runs[:limit],'container':None,'containers':containers[:limit],
            'raw_days':config.METRICS_RAW_DAYS,'interval_s':config.METRICS_INTERVAL_S,
            'truncated':len(runs)>limit or len(containers)>limit,'scope':'reviewed_cross_user_operator_read','registry_version':authority['registry_version']}
