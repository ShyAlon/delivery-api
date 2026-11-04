from registered_runner import registered_runner
import pytest
from uuid import uuid4
from vmodel_contracts.progress_signal import ProgressSignal,ProgressPage
from vmodel_contracts.interactive import EngineSnapshot,InputRequest,InputKind,ProcessStatus,NextStep
from delivery_api.postgres_store import PostgresStore
from delivery_api.progress_adapter import ingest_interview_page,ingest_generator_line,PREFIX
from delivery_api.control_snapshot import project


@pytest.fixture
def progress_store(pg_workspace_factory,monkeypatch):
    from delivery_api import app as module
    from delivery_api.tenancy import tenant_context
    from pg_storage_guard import assert_pg_storage_only
    with pg_workspace_factory() as workspace:
        monkeypatch.setattr(module,'store',workspace.store)
        monkeypatch.setitem(module.app.state._state,'store',workspace.store)
        monkeypatch.setattr(module.orchestrator,'store',workspace.store)
        assert_pg_storage_only()
        with tenant_context(workspace.scope):
            workspace.store._fixture_dsn=workspace.dsn
            yield workspace.store


def signal(task_id,session_id,phase,cursor=1,event_id=None):
    return ProgressSignal(event_id=event_id or str(uuid4()),cursor=cursor,task_id=task_id,session_id=session_id,
        phase_key=phase,stage='stage',label='Working',status='started')


def test_cursor_dedupe_restart_and_control_isolation(tmp_path,progress_store):
    store=progress_store;task=store.create_task('Protocol isolated')
    sid=str(uuid4());row=signal(task['task_id'],sid,'intent')
    ingest_interview_page(store,task['task_id'],sid,ProgressPage(events=[row],next_cursor=1))
    assert store.progress_cursor(task['task_id'],'interview',sid)==1
    assert len([e for e in store.events(task['task_id']) if e['kind']=='progress'])==1
    store=PostgresStore(progress_store._fixture_dsn.replace('user=sandbox','user=delivery_app'))
    ingest_interview_page(store,task['task_id'],sid,ProgressPage(events=[row],next_cursor=1))
    assert len([e for e in store.events(task['task_id']) if e['kind']=='progress'])==1
    assert ingest_generator_line(store,task['task_id'],PREFIX+signal(task['task_id'],sid,'tests').model_dump_json(),'tests')
    assert ingest_generator_line(store,task['task_id'],PREFIX+signal('other',sid,'tests').model_dump_json(),'tests')
    assert ingest_generator_line(store,task['task_id'],PREFIX+signal(task['task_id'],'wrong-session','tests').model_dump_json(),'tests',sid)
    assert len([e for e in store.events(task['task_id']) if e['kind']=='progress'])==2
    assert store.task(task['task_id'])['state']=='not_started'


def test_engine_and_gate_have_same_shape_not_shared_sequence(tmp_path,progress_store):
    store=progress_store;task=store.create_task('Control isolated')
    store.update_phase(task['task_id'],'intent','running');task=store.task(task['task_id'])
    pending=InputRequest(input_id=uuid4(),kind=InputKind.TEXT,prompt='Question?')
    engine=EngineSnapshot(session_id='s',task_id=task['task_id'],sequence=7,process_status=ProcessStatus.WAITING_INPUT,
                          next_step=NextStep.WAIT_INPUT,open_input=pending)
    control=project(task,{'waiting':True},engine)
    assert control.mode=='waiting_input' and control.precondition['session_sequence']==7
    assert control.wait_id==str(pending.input_id)
    store.wait_at_gate(task['task_id'],'intent')
    gate=project(store.task(task['task_id']),{'waiting':False},artifacts=[{'phase_key':'intent','ref':'/tmp/intent.json','present':True,'valid':True}])
    assert gate.mode=='waiting_gate' and gate.precondition['gate_revision']>0
    assert gate.artifact_ref=='/tmp/intent.json'
    assert 'session_sequence' not in gate.precondition
    with __import__('pytest').raises(ValueError):store.decide_gate(task['task_id'],gate.wait_id,'approve',selected_option='Approve Start',expected_revision=0)


def test_successful_subprocess_without_configured_artifact_fails_without_gate(tmp_path,progress_store,monkeypatch,registered_runner):
    import asyncio,sys
    from delivery_api.runner import ProcessRunner
    store=progress_store;task=store.create_task('No false gate')
    store.set_engine_session(task['task_id'],'fake-session')
    runner=ProcessRunner(store)
    result=asyncio.run(runner.run(task['task_id'],[sys.executable,'-c','print("finished")'],cwd=tmp_path,phase='tests',env={'VMODEL_PROGRESS_TASK_ID':task['task_id'],'VMODEL_PROGRESS_SESSION_ID':'fake-session'},infer_phase=False))
    assert result!=0
    current=store.task(task['task_id'])
    assert next(p for p in current['phases'] if p['phase_key']=='tests')['state']=='failed'
    assert next(g for g in current['gates'] if g['after_phase']=='tests')['state']=='idle'


@pytest.mark.engine_integration
def test_http_client_and_delivery_ingest_read_during_engine_start(tmp_path,progress_store):
    import threading
    from interview_engine.http_service import SessionRegistry,create_app
    from interview_engine.pg_journal import ObservationalJournal
    from interview_engine.pg_session_store import PgSessionStore
    from delivery_api.tenancy import current_scope
    from delivery_api.db_pool import pool_for
    from vmodel_contracts.interactive import StartSessionRequest
    from concurrent.futures import ThreadPoolExecutor
    entered=threading.Event();release=threading.Event();session=str(uuid4())
    store=progress_store;task=store.create_task('Start progress')
    scope=current_scope()
    pg_store=PgSessionStore(pool_for(store._fixture_dsn.replace('user=sandbox','user=delivery_app')),claims=(scope.owner_id,scope.session_id))
    class Program:
        model_map=None
        def advance(self):
            ObservationalJournal(pg_store,registry._sessions[session].pg_claim,session,task['task_id']).append(phase_key='intent',stage='probe',label='Probing',status='started')
            entered.set();assert release.wait(3)
            return InputRequest(input_id=uuid4(),kind=InputKind.TEXT,prompt='Who?')
    registry=SessionRegistry(lambda _sid,_req:Program(),session_store=pg_store,owner_id=scope.owner_id,progress_mode='observational')
    app=create_app(registry,service_token='test')
    from fastapi.testclient import TestClient
    client=TestClient(app,headers={'X-Interview-Engine-Token':'test'})
    with ThreadPoolExecutor() as pool:
        reply=pool.submit(client.post,'/v1/sessions',json=StartSessionRequest(task_id=task['task_id'],session_id=__import__('uuid').UUID(session),idea='idea',model_route='m').model_dump(mode='json'))
        assert entered.wait(2)
        page=ProgressPage.model_validate(client.get(f'/v1/sessions/{session}/progress?after=0').json())
        ingest_interview_page(store,task['task_id'],session,page)
        assert store.progress_cursor(task['task_id'],'interview',session)==1
        assert store.events(task['task_id'])[-1]['payload']['stage']=='probe'
        release.set();assert reply.result(timeout=3).status_code==201
    registry.close()
    # Retain published journal evidence instead of violating its session FK.
    store.delete_task(task['task_id'])
    assert store.task(task['task_id']) is None
    assert pg_store.read_journal(session,scope.owner_id,0,100)


def test_every_generator_phase_uses_one_progress_and_gate_boundary(tmp_path,progress_store,monkeypatch,registered_runner):
    """Installed-style subprocess path: framed signal and real artifact precede each gate."""
    import asyncio
    import sys
    from delivery_api.phases import PHASE_DEFINITIONS
    from delivery_api.runner import ProcessRunner

    for definition in PHASE_DEFINITIONS:
        if definition.executor != "generator":
            continue
        out = tmp_path / definition.key
        out.mkdir()
        store = progress_store
        task_id = store.create_task(f"Progress {definition.key}")["task_id"]
        sid = str(uuid4())
        store.set_engine_session(task_id,sid)
        progress = signal(task_id, sid, definition.key).model_dump_json()
        artifact = definition.artifacts[0]
        payload = '[]' if definition.key == 'tests' else '{"session_id":"s","items":[]}' if definition.key == 'validation' else '{}'
        script = ("from pathlib import Path; import sys; "
                  "out=Path(sys.argv[sys.argv.index('--out')+1]); "
                  f"(out/{artifact!r}).write_text({payload!r}); "
                  f"print({(PREFIX + progress)!r},flush=True)")
        from test_http_cutover import _PGUsage
        _PGUsage(store,task_id,'spec/'+artifact).write_text(payload)
        runner = ProcessRunner(store)
        result = asyncio.run(runner.run(task_id, [sys.executable, "-c", script, "--out", str(out)],
                                        cwd=tmp_path, phase=definition.key,
                                        env={"VMODEL_PROGRESS_TASK_ID": task_id,
                                             "VMODEL_PROGRESS_SESSION_ID": sid}, infer_phase=False))
        assert result == 0, definition.key
        current = store.task(task_id)
        assert current["active_phase"] == definition.key
        assert current["state"] == "waiting_gate"
        assert next(g for g in current["gates"] if g["after_phase"] == definition.key)["state"] == "waiting"
        assert len([e for e in store.events(task_id) if e["kind"] == "progress"
                    and e["phase_key"] == definition.key and e["payload"]["source"] == "generator"]) == 1
        assert project(current, {"process_live": False}, artifacts=[{"phase_key": definition.key,
                       "ref": str(out / artifact), "present": True, "valid": True}]).mode == "waiting_gate"


def test_progress_page_timing_measures_fetch_ingest_and_source_age(tmp_path,monkeypatch,progress_store):
    import asyncio
    from delivery_api import app as module
    store=progress_store;task=store.create_task('Timing');sid=str(uuid4())
    row=signal(task['task_id'],sid,'intent');reports=[]
    monkeypatch.setattr(module,'store',store)
    class Engine:
        def progress(self,session,cursor):return ProgressPage(events=[row],next_cursor=1) if cursor==0 else ProgressPage(events=[],next_cursor=1)
    monkeypatch.setattr(module,'engine_client',Engine())
    monkeypatch.setattr(module,'activity',lambda name,message,**fields:reports.append((name,fields)))
    asyncio.run(module._drain_engine_progress(task['task_id'],sid))
    timing=[fields for name,fields in reports if name=='progress.ingest.timing']
    assert len(timing)==1 and timing[0]['events']==1
    assert all(timing[0][key]>=0 for key in ('fetch_ms','ingest_ms','source_age_max_ms','source_age_min_ms'))
    assert timing[0]['next_cursor']==1


def test_progress_fixture_is_restricted_postgres(progress_store):
    from delivery_api.postgres_store import PostgresStore
    from pg_storage_guard import assert_pg_storage_only
    assert isinstance(progress_store,PostgresStore)
    assert_pg_storage_only()
    with progress_store._postgres.transaction() as connection:
        assert connection.execute('SELECT current_user').fetchone()['current_user']=='requi_backend'
