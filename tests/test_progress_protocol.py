from uuid import uuid4
from vmodel_contracts.progress_signal import ProgressSignal,ProgressPage
from vmodel_contracts.interactive import EngineSnapshot,InputRequest,InputKind,ProcessStatus,NextStep
from delivery_api.store import SQLiteStore
from delivery_api.progress_adapter import ingest_interview_page,ingest_generator_line,PREFIX
from delivery_api.control_snapshot import project


def signal(task_id,session_id,phase,cursor=1,event_id=None):
    return ProgressSignal(event_id=event_id or str(uuid4()),cursor=cursor,task_id=task_id,session_id=session_id,
        phase_key=phase,stage='stage',label='Working',status='started')


def test_cursor_dedupe_restart_and_control_isolation(tmp_path):
    path=tmp_path/'tasks.db';store=SQLiteStore(str(path));task=store.create_task('Protocol isolated')
    sid=str(uuid4());row=signal(task['task_id'],sid,'intent')
    ingest_interview_page(store,task['task_id'],sid,ProgressPage(events=[row],next_cursor=1))
    assert store.progress_cursor(task['task_id'],'interview',sid)==1
    assert len([e for e in store.events(task['task_id']) if e['kind']=='progress'])==1
    store=SQLiteStore(str(path))
    ingest_interview_page(store,task['task_id'],sid,ProgressPage(events=[row],next_cursor=1))
    assert len([e for e in store.events(task['task_id']) if e['kind']=='progress'])==1
    assert ingest_generator_line(store,task['task_id'],PREFIX+signal(task['task_id'],sid,'tests').model_dump_json(),'tests')
    assert ingest_generator_line(store,task['task_id'],PREFIX+signal('other',sid,'tests').model_dump_json(),'tests')
    assert ingest_generator_line(store,task['task_id'],PREFIX+signal(task['task_id'],'wrong-session','tests').model_dump_json(),'tests',sid)
    assert len([e for e in store.events(task['task_id']) if e['kind']=='progress'])==2
    assert store.task(task['task_id'])['state']=='not_started'


def test_engine_and_gate_have_same_shape_not_shared_sequence(tmp_path):
    store=SQLiteStore(str(tmp_path/'tasks.db'));task=store.create_task('Control isolated')
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


def test_successful_subprocess_without_configured_artifact_fails_without_gate(tmp_path):
    import asyncio,sys
    from delivery_api.runner import ProcessRunner
    store=SQLiteStore(str(tmp_path/'tasks.db'));task=store.create_task('No false gate')
    runner=ProcessRunner(store)
    result=asyncio.run(runner.run(task['task_id'],[sys.executable,'-c','print("finished")'],cwd=tmp_path,phase='tests',env={'VMODEL_PROGRESS_TASK_ID':task['task_id'],'VMODEL_PROGRESS_SESSION_ID':'fake-session'},infer_phase=False))
    assert result!=0
    current=store.task(task['task_id'])
    assert next(p for p in current['phases'] if p['phase_key']=='tests')['state']=='failed'
    assert next(g for g in current['gates'] if g['after_phase']=='tests')['state']=='idle'


def test_http_client_and_delivery_ingest_read_during_engine_start(tmp_path):
    import threading
    from interview_engine.http_service import SessionRegistry,create_app
    from interview_engine.progress_journal import ProgressJournal
    from vmodel_contracts.interactive import StartSessionRequest
    from concurrent.futures import ThreadPoolExecutor
    entered=threading.Event();release=threading.Event();session=str(uuid4())
    store=SQLiteStore(str(tmp_path/'delivery.db'));task=store.create_task('Start progress')
    class Program:
        model_map=None
        def advance(self):
            ProgressJournal(tmp_path,session,task['task_id']).append(phase_key='intent',stage='probe',label='Probing',status='started')
            entered.set();assert release.wait(3)
            return InputRequest(input_id=uuid4(),kind=InputKind.TEXT,prompt='Who?')
    app=create_app(SessionRegistry(lambda _sid,_req:Program(),state_dir=tmp_path),service_token='test')
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


def test_every_generator_phase_uses_one_progress_and_gate_boundary(tmp_path):
    """Installed-style subprocess path: framed signal and real artifact precede each gate."""
    import asyncio
    import sys
    from delivery_api.phases import PHASE_DEFINITIONS
    from delivery_api.runner import ProcessRunner

    for definition in PHASE_DEFINITIONS:
        if definition.executor != "generator":
            continue
        db = tmp_path / f"{definition.key}.db"
        out = tmp_path / definition.key
        out.mkdir()
        store = SQLiteStore(str(db))
        task_id = store.create_task(f"Progress {definition.key}")["task_id"]
        sid = str(uuid4())
        progress = signal(task_id, sid, definition.key).model_dump_json()
        artifact = definition.artifacts[0]
        payload = '[]' if definition.key == 'tests' else '{"session_id":"s","items":[]}' if definition.key == 'validation' else '{}'
        script = ("from pathlib import Path; import sys; "
                  "out=Path(sys.argv[sys.argv.index('--out')+1]); "
                  f"(out/{artifact!r}).write_text({payload!r}); "
                  f"print({(PREFIX + progress)!r},flush=True)")
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
