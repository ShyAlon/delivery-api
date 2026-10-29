"""Additive, explicitly selected PG resource-metrics repository.

Not wired into the sampler yet: schema/role rollout, historical import and
activation remain separate. Uses the shared process pool, no SQLite fallback,
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
        with pool_for(self.dsn).connection() as c:
            with c.transaction():
                c.execute('SET LOCAL ROLE requi_metrics_writer')
                c.execute("SELECT set_config('requi.metrics_source',%s,true)",(self.source_id,))
                # Serializes append/rollup per source, never across hosts. Prevents
                # late raw rows racing the summary/delete transaction.
                c.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',('resource-metrics:'+self.source_id,))
                yield c

    def append_run(self,**row):
        fields=('ts','run_key','task_id','session_id','phase','pid','procs','rss_mb','peak_rss_mb','cpu_seconds','cpu_pct')
        if set(row)!=set(fields):raise ValueError('exact run sample required')
        with self.transaction() as c:
            if c.execute('SELECT 1 FROM public.resource_run_summaries WHERE run_key=%s',(row['run_key'],)).fetchone():
                raise ValueError('run already rolled up; late sample requires explicit reconciliation')
            c.execute('INSERT INTO public.resource_run_samples(source_id,'+','.join(fields)+') VALUES ('+','.join(['%s']*12)+')',(self.source_id,*(row[f] for f in fields)))

    def append_container(self,**row):
        fields=('ts','mem_used_mb','mem_limit_mb','cpu_seconds','cpu_pct')
        if set(row)!=set(fields):raise ValueError('exact container sample required')
        with self.transaction() as c:
            if c.execute("SELECT 1 FROM public.resource_container_summaries WHERE day=to_char(to_timestamp(%s) AT TIME ZONE 'UTC','YYYY-MM-DD')",(row['ts'],)).fetchone():
                raise ValueError('day already rolled up; late sample requires explicit reconciliation')
            c.execute('INSERT INTO public.resource_container_samples(source_id,'+','.join(fields)+') VALUES ('+','.join(['%s']*6)+')',(self.source_id,*(row[f] for f in fields)))

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

def _round(value):return None if value is None else round(value,1)
