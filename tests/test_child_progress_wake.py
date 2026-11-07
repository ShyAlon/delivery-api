"""Commit-only wakes keep real progress prompt during idle polling backoff."""
import asyncio
import time
from uuid import uuid4
from delivery_api.child_progress import progress_wake_channel,install_progress_wakes


def test_real_committed_progress_wakes_a_backed_off_watcher(pg_scoped_workspace,monkeypatch):
    from delivery_api import app as module,event_bus
    from delivery_api.db_pool import pool_for
    from interview_engine.pg_session_store import PgSessionStore
    from interview_engine.pg_journal import ObservationalJournal
    from vmodel_contracts.progress_signal import ProgressPage,ProgressSignal
    w=pg_scoped_workspace;store=w.store;sid=str(uuid4())
    tid=store.create_task('Prompt progress after idle')['task_id'];store.set_engine_session(tid,sid)
    source=PgSessionStore(pool_for(store._postgres.dsn),claims=(w.scope.owner_id,w.scope.session_id))
    source.create(sid,tid,w.scope.owner_id)
    claim=source.claim(sid,w.scope.owner_id,'wake-test',ttl=60)
    journal=ObservationalJournal(source,claim,sid,tid)
    class Engine:
        def progress(self,session,cursor):
            rows=source.read_journal(session,w.scope.owner_id,cursor,100)
            events=[ProgressSignal.model_validate({k:v for k,v in row['page'].items() if k!='journal_mode'}) for row in rows]
            return ProgressPage(events=events,next_cursor=events[-1].cursor if events else cursor)
    monkeypatch.setattr(module,'store',store);monkeypatch.setattr(module,'engine_client',Engine())
    idle=__import__('threading').Event();wait=event_bus.wait
    def measured_wait(key,seconds,*args):
        if key=='engine-progress:'+sid and seconds>=8:idle.set()
        return wait(key,seconds,*args)
    monkeypatch.setattr(event_bus,'wait',measured_wait)
    async def go():
        stop=asyncio.Event();watch=asyncio.create_task(module._watch_engine_advance(tid,sid,stop))
        try:
            assert await asyncio.to_thread(idle.wait,12),'watcher did not back off'
            with progress_wake_channel(sid) as fd:
                channel=install_progress_wakes(source,__import__('os').dup(fd.fileno()))
                try:
                    at=time.monotonic()
                    journal.append(phase_key='intent',stage='probe',label='Wake proof',status='started')
                    while not any(e.get('message')=='Wake proof' for e in store.events(tid)):
                        assert time.monotonic()-at<0.75,'committed event waited for the safety poll'
                        await asyncio.sleep(.02)
                    print('COMMIT_TO_INGEST_MS',int((time.monotonic()-at)*1000))
                finally:channel.close()
        finally:stop.set();await watch
    asyncio.run(go())


def test_failed_commit_does_not_wake(monkeypatch):
    import os
    from delivery_api import event_bus
    class Store:
        def publish_step(self,*a,**k):raise RuntimeError('rolled back')
        def append_observational_progress(self,*a,**k):raise RuntimeError('rolled back')
    store=Store();key='engine-progress:rollback';version=event_bus.version(key)
    with progress_wake_channel('rollback') as fd:
        channel=install_progress_wakes(store,os.dup(fd.fileno()))
        try:
            import pytest
            with pytest.raises(RuntimeError):store.publish_step(journal_pages=[(1,{})])
            assert not event_bus.wait(key,.1,since=version)
        finally:channel.close()
