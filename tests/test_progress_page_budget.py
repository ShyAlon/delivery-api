"""Write budget and atomicity on owned disposable PG, no provider calls."""
import asyncio
import time
from uuid import uuid4
import pytest
from delivery_api.progress_adapter import ingest_interview_page
from delivery_api.postgres_scope import RequestStats,request_stats
from delivery_api.tenancy import tenant_context,TenantAccessDenied
from vmodel_contracts.progress_signal import ProgressPage,ProgressSignal


def page(task,sid,statuses):
    return ProgressPage(events=[ProgressSignal(event_id=str(uuid4()),cursor=i+1,task_id=task,
        session_id=sid,phase_key='intent',stage='probe' if status!='call' else 'llm_call:probe',
        label='Working',status='completed' if status=='call' else status) for i,status in enumerate(statuses)],next_cursor=len(statuses))


def test_page_budget_and_filter(pg_scoped_workspace):
    w=pg_scoped_workspace;s=w.store;t=s.create_task('Budget');sid=str(uuid4())
    p=page(t['task_id'],sid,['started','heartbeat','call','completed','heartbeat'])
    stats=RequestStats();tok=request_stats.set(stats)
    try:ingest_interview_page(s,t['task_id'],sid,p)
    finally:request_stats.reset(tok)
    print('PAGE_BUDGET',stats.queries,stats.transactions,stats.statements)
    assert stats.transactions==1
    assert stats.queries<=5
    assert len([e for e in s.events(t['task_id']) if e['kind']=='progress'])==2
    assert s.progress_cursor(t['task_id'],'interview',sid)==5
    ingest_interview_page(s,t['task_id'],sid,p)
    assert len([e for e in s.events(t['task_id']) if e['kind']=='progress'])==2


def test_page_gap_is_atomic(pg_scoped_workspace):
    w=pg_scoped_workspace;s=w.store;t=s.create_task('Atomic');sid=str(uuid4())
    p=page(t['task_id'],sid,['started','completed']);p.events[1].cursor=3;p.next_cursor=3
    with pytest.raises(ValueError):ingest_interview_page(s,t['task_id'],sid,p)
    assert s.progress_cursor(t['task_id'],'interview',sid)==0
    assert not [e for e in s.events(t['task_id']) if e['kind']=='progress']


def test_page_tail_mismatch_is_atomic(pg_scoped_workspace):
    w=pg_scoped_workspace;s=w.store;t=s.create_task('Tail');sid=str(uuid4())
    p=page(t['task_id'],sid,['started']);p.next_cursor=2
    with pytest.raises(ValueError):ingest_interview_page(s,t['task_id'],sid,p)
    assert s.progress_cursor(t['task_id'],'interview',sid)==0


def test_db_work_does_not_block_loop(pg_scoped_workspace,monkeypatch):
    from delivery_api import app as module
    s=pg_scoped_workspace.store;t=s.create_task('Loop');sid=str(uuid4());p=page(t['task_id'],sid,['started'])
    monkeypatch.setattr(module,'store',s)
    class Engine:
        def progress(self,session,cursor):return p if not cursor else ProgressPage(events=[],next_cursor=1)
    monkeypatch.setattr(module,'engine_client',Engine())
    original=s._execute_native
    def delayed(*args,**kwargs):time.sleep(.01);return original(*args,**kwargs)
    monkeypatch.setattr(s,'_execute_native',delayed)
    async def run():
        beats=[];done=False
        async def clock():
            while not done:beats.append(time.monotonic());await asyncio.sleep(.002)
        clock_task=asyncio.create_task(clock())
        try:await module._drain_engine_progress(t['task_id'],sid)
        finally:done=True;await clock_task
        assert len(beats)>5
        assert max(b-a for a,b in zip(beats,beats[1:]))<.04
    asyncio.run(run())


def test_cross_owner_and_revoked_session_fail_closed(pg_workspace_factory):
    with pg_workspace_factory() as a,pg_workspace_factory() as b:
        with tenant_context(a.scope):t=a.store.create_task('Private')
        p=page(t['task_id'],str(uuid4()),['started'])
        with tenant_context(b.scope),pytest.raises(KeyError):
            ingest_interview_page(b.store,t['task_id'],p.events[0].session_id,p)
        with a.admin() as c:c.execute('DELETE FROM auth.sessions WHERE id=%s',(a.scope.session_id,))
        with tenant_context(a.scope),pytest.raises(KeyError):
            ingest_interview_page(a.store,t['task_id'],p.events[0].session_id,p)


def test_page_sql_failure_rolls_back_every_write(pg_scoped_workspace,monkeypatch):
    s=pg_scoped_workspace.store;t=s.create_task('Rollback');sid=str(uuid4());p=page(t['task_id'],sid,['started','completed'])
    original=s._execute_native
    def fail(sql,*args,**kwargs):
        result=original(sql,*args,**kwargs)
        if sql.startswith('WITH source_cursor'):raise RuntimeError('after writes before commit')
        return result
    with monkeypatch.context() as m:
        m.setattr(s,'_execute_native',fail)
        with pytest.raises(RuntimeError):ingest_interview_page(s,t['task_id'],sid,p)
    assert s.progress_cursor(t['task_id'],'interview',sid)==0
    assert not [e for e in s.events(t['task_id']) if e['kind']=='progress']
    ingest_interview_page(s,t['task_id'],sid,p)
    assert len([e for e in s.events(t['task_id']) if e['kind']=='progress'])==2


def test_concurrent_same_page_copies_once(pg_scoped_workspace):
    from concurrent.futures import ThreadPoolExecutor
    w=pg_scoped_workspace;s=w.store;t=s.create_task('Race');sid=str(uuid4());p=page(t['task_id'],sid,['started','heartbeat','completed'])
    def consume():
        with tenant_context(w.scope):ingest_interview_page(s,t['task_id'],sid,p)
    with ThreadPoolExecutor(2) as pool:list(pool.map(lambda _:consume(),range(2)))
    assert s.progress_cursor(t['task_id'],'interview',sid)==3
    assert len([e for e in s.events(t['task_id']) if e['kind']=='progress'])==2
    assert s.task(t['task_id'])['state']=='not_started'


def test_page_measurement_against_original_path(pg_scoped_workspace,monkeypatch):
    s=pg_scoped_workspace.store
    original=s._execute_native
    def delayed(*args,**kwargs):time.sleep(.01);return original(*args,**kwargs)
    monkeypatch.setattr(s,'_execute_native',delayed)
    samples=[]
    for batch in (False,True):
        t=s.create_task('Measure '+str(batch));sid=str(uuid4());p=page(t['task_id'],sid,['started','heartbeat','call','completed','heartbeat'])
        stats=RequestStats();token=request_stats.set(stats);start=time.monotonic()
        try:
            if batch:ingest_interview_page(s,t['task_id'],sid,p)
            else:
                # Original adapter: one cursor read, then one transaction per signal.
                assert p.next_cursor>=s.progress_cursor(t['task_id'],'interview',sid)
                for signal in p.events:s.ingest_progress(signal,'interview')
        finally:request_stats.reset(token)
        samples.append((stats.queries,stats.transactions,round((time.monotonic()-start)*1000)))
    print('BEFORE_AFTER_10MS',samples)
    assert samples[0][:2]==(47,6)
    assert samples[1][:2]==(3,1)
    assert samples[1][2]<samples[0][2]/3


def test_phase_counts_survive_later_boundary_without_counts(pg_scoped_workspace):
    s=pg_scoped_workspace.store;t=s.create_task('Counts');sid=str(uuid4())
    s.update_phase(t['task_id'],'intent','running')
    p=page(t['task_id'],sid,['item_completed','completed'])
    p.events[0].completed=2;p.events[0].total=7
    ingest_interview_page(s,t['task_id'],sid,p)
    phase=next(p for p in s.task(t['task_id'])['phases'] if p['phase_key']=='intent')
    assert (phase['completed'],phase['total'])==(2,7)


def test_combined_source_and_delivery_budget(pg_scoped_workspace):
    """One five-signal stream through real source lease/journal and delivery RLS."""
    from contextlib import contextmanager
    from interview_engine.pg_session_store import PgSessionStore
    from interview_engine.pg_journal import ObservationalJournal
    from interview_engine.application import durable_progress_signal
    from interview_engine.progress import ProgressEvent
    w=pg_scoped_workspace;s=w.store;results=[]
    for filtered in (False,True):
        t=s.create_task('Combined '+str(filtered));sid=str(uuid4());s.set_engine_session(t['task_id'],sid)
        source=PgSessionStore(s._postgres._pool,claims=(w.scope.owner_id,w.scope.session_id))
        source.create(sid,t['task_id'],w.scope.owner_id);claim=source.claim(sid,w.scope.owner_id,'budget',ttl=60)
        journal=ObservationalJournal(source,claim,sid,t['task_id']);borrow=source._borrow
        counts={'source_queries':0,'source_transactions':0}
        class Probe:
            def __init__(self,c):self.c=c
            def execute(self,*args,**kwargs):counts['source_queries']+=1;time.sleep(.01);return self.c.execute(*args,**kwargs)
            def commit(self):counts['source_transactions']+=1;return self.c.commit()
            def __getattr__(self,k):return getattr(self.c,k)
        @contextmanager
        def measured():
            with borrow() as c:yield Probe(c)
        source._borrow=measured
        original=s._execute_native
        def delayed(*args,**kwargs):time.sleep(.01);return original(*args,**kwargs)
        s._execute_native=delayed
        start=time.monotonic();signals=[];token=request_stats.set(RequestStats())
        try:
            for status in ('started','heartbeat','call','completed','heartbeat'):
                event=ProgressEvent('llm_call:probe' if status=='call' else 'probe','Probing','completed' if status=='call' else status,0)
                if filtered and not durable_progress_signal(event):continue
                signals.append(journal.append(phase_key='intent',stage=event.stage,label=event.label,status=event.status))
            p=ProgressPage(events=signals,next_cursor=len(signals))
            if filtered:ingest_interview_page(s,t['task_id'],sid,p)
            else:
                assert p.next_cursor>=s.progress_cursor(t['task_id'],'interview',sid)
                for signal in p.events:s.ingest_progress(signal,'interview')
            stats=request_stats.get()
        finally:request_stats.reset(token);s._execute_native=original
        counts.update(source_rows=len(signals),delivery_queries=stats.queries,delivery_transactions=stats.transactions,
                      wall_ms=round((time.monotonic()-start)*1000))
        results.append(counts)
    print('COMBINED_BEFORE_AFTER_10MS',results)
    assert (results[0]['source_queries'],results[0]['source_transactions'],results[0]['source_rows'])==(15,5,5)
    assert (results[1]['source_queries'],results[1]['source_transactions'],results[1]['source_rows'])==(6,2,2)
    assert (results[0]['delivery_queries'],results[1]['delivery_queries'])==(47,3)


def test_current_page_is_four_wire_syncs_and_preserves_overlap(pg_scoped_workspace):
    s=pg_scoped_workspace.store;t=s.create_task('Wire budget');sid=str(uuid4())
    p=page(t['task_id'],sid,['started','heartbeat','call','completed'])
    first=p.model_copy(update={'events':p.events[:2],'next_cursor':2})
    ingest_interview_page(s,t['task_id'],sid,first)
    stats=RequestStats();token=request_stats.set(stats)
    try:assert ingest_interview_page(s,t['task_id'],sid,p)==1
    finally:request_stats.reset(token)
    print('CURRENT_PROGRESS_SYNC',sum(e[3] for e in stats.trace),stats.queries,stats.transactions)
    assert sum(e[3] for e in stats.trace)==4  # identity, lock, cursor/write CTE, commit
    assert s.progress_cursor(t['task_id'],'interview',sid)==4
    assert len([e for e in s.events(t['task_id']) if e['kind']=='progress'])==2
    with pytest.raises(ValueError,match='backwards'):ingest_interview_page(s,t['task_id'],sid,first)
    assert s.progress_cursor(t['task_id'],'interview',sid)==4


def test_answer_observation_never_advances_durable_cursor(pg_scoped_workspace):
    from delivery_api.progress_adapter import ingest_answer_page
    s=pg_scoped_workspace.store;t=s.create_task('Separate streams');sid=str(uuid4())
    p=page(t['task_id'],sid,['started','completed'])
    ingest_interview_page(s,t['task_id'],sid,p)
    transient=page(t['task_id'],sid,['started','heartbeat','call','completed','failed'])
    for signal in transient.events:signal.cursor+=10**15
    transient.next_cursor+=10**15
    assert ingest_answer_page(s,t['task_id'],sid,transient)==3
    assert s.progress_cursor(t['task_id'],'interview',sid)==2
    count=len(s.events(t['task_id']))
    ingest_answer_page(s,t['task_id'],sid,transient)
    assert len(s.events(t['task_id']))==count
    # After a parent restart only the committed source journal resumes here.
    next_page=page(t['task_id'],sid,['started','completed','started'])
    ingest_interview_page(s,t['task_id'],sid,next_page)
    assert s.progress_cursor(t['task_id'],'interview',sid)==3


def test_answer_observation_foreign_and_revoked_fail_closed(pg_workspace_factory):
    from delivery_api.progress_adapter import ingest_answer_page
    with pg_workspace_factory() as a,pg_workspace_factory() as b:
        with tenant_context(a.scope):t=a.store.create_task('Answer privacy')
        p=page(t['task_id'],str(uuid4()),['started'])
        with tenant_context(b.scope),pytest.raises(KeyError):ingest_answer_page(a.store,t['task_id'],p.events[0].session_id,p)
        with a.admin() as c:c.execute('DELETE FROM auth.sessions WHERE id=%s',(a.scope.session_id,))
        with tenant_context(a.scope),pytest.raises(KeyError):ingest_answer_page(a.store,t['task_id'],p.events[0].session_id,p)


def test_answer_observation_write_failure_rolls_back(pg_scoped_workspace,monkeypatch):
    from delivery_api.progress_adapter import ingest_answer_page
    s=pg_scoped_workspace.store;t=s.create_task('Answer rollback');sid=str(uuid4())
    p=page(t['task_id'],sid,['started','completed']);original=s._execute_native
    def fail(sql,*a,**kw):
        result=original(sql,*a,**kw)
        if sql.startswith('WITH source_cursor'):raise RuntimeError('answer stage write failed')
        return result
    with monkeypatch.context() as m:
        m.setattr(s,'_execute_native',fail)
        with pytest.raises(RuntimeError):ingest_answer_page(s,t['task_id'],sid,p)
    assert not s.events(t['task_id'])
    assert s.progress_cursor(t['task_id'],'interview',sid)==0
    assert ingest_answer_page(s,t['task_id'],sid,p)==2
