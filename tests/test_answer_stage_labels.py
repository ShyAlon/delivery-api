"""Answer in-flight stage labels and waiting-state contradictions.

Fail-first on main: answerProcessingLabel does not exist, the shadow is
written only after the POST returns, and signal age renders while waiting.
"""
import shutil, subprocess, textwrap
from pathlib import Path
import pytest

WEB = Path(__file__).resolve().parents[1] / 'src' / 'delivery_api' / 'web'


def _web(name):
    return (WEB / name).read_text()


def _run(tmp_path, body):
    for source in WEB.glob('*.js'):
        (tmp_path / source.name).write_text(source.read_text())
    run = tmp_path / 'run.mjs'
    # Shared navigation instrumentation wraps native history methods at import.
    # These pure label tests need the browser API shape, not a real navigation.
    run.write_text('globalThis.history={pushState(){},replaceState(){}};\n'+textwrap.dedent(body))
    result = subprocess.run(['node', str(run)], capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


@pytest.mark.skipif(not shutil.which('node'), reason='node not installed')
def test_stale_signals_never_refine_the_in_flight_label(tmp_path):
    out = _run(tmp_path, """
        globalThis.window={addEventListener(){}};
        globalThis.document={addEventListener(){},querySelectorAll:()=>[]};
        const {answerProcessingLabel}=await import('./progress.js');
        const stale=[{id:1,kind:'progress',message:'Coverage',ts:1000}];
        const fresh=[{id:1,kind:'progress',message:'Coverage',ts:1000},{id:2,kind:'progress',message:'coverage',ts:2000}];
        console.log(JSON.stringify({
          noSubmit: answerProcessingLabel(fresh,{phaseKey:'intent'}),
          staleOnly: answerProcessingLabel(stale,{phaseKey:'intent',sinceMs:1500000}),
          fresh: answerProcessingLabel(fresh,{phaseKey:'intent',sinceMs:1500000}),
        }));
        """)
    import json
    got = json.loads(out)
    assert got['noSubmit'] == 'Processing your answer…'
    assert got['staleOnly'] == 'Processing your answer…', 'a signal from before submit must not name the stage'
    assert got['fresh'] == 'Processing your answer · coverage'


@pytest.mark.skipif(not shutil.which('node'), reason='node not installed')
def test_signal_age_hidden_while_waiting_for_the_user(tmp_path):
    out = _run(tmp_path, """
        globalThis.window={addEventListener(){}};
        globalThis.document={addEventListener(){},querySelectorAll:()=>[]};
        const {stageProgressHtml}=await import('./progress.js');
        const events=[{id:1,kind:'progress',message:'Coverage',ts:1000}];
        console.log(JSON.stringify({
          waiting: stageProgressHtml(events,{startedAt:1000,signalAge:false}),
          working: stageProgressHtml(events,{startedAt:1000,signalAge:true}),
        }));
        """)
    import json
    got = json.loads(out)
    assert 'last update' not in got['waiting'], 'signal age while waiting masquerades as a stall'
    assert 'last update' in got['working']


def test_shadow_is_written_at_submit_not_at_response():
    text = _web('task.js')
    submit = text.index("form.dataset.sending='true'")
    shadow_write = text.index('sessionStorage.setItem(shadowKey')
    await_post = text.index('await api(`/api/tasks/${id}/answer`')
    assert submit < shadow_write < await_post, 'the in-flight shadow must exist before the POST is in flight'


def test_render_time_in_flight_reads_the_persisted_flag():
    text = _web('task.js')
    assert 't.answer_in_flight===true' in text, 'a mid-POST re-render must keep the processing state from server truth'
    assert 'currentQuestion&&!answerPending' in text, 'the answered question must hide while the answer is in flight'
    assert 'answerPending=Boolean(shadow)||t.answer_in_flight===true' in text


def test_answer_in_flight_flag_derives_only_from_engine_truth():
    from delivery_api.app import _answer_in_flight
    base = {"engine_session_id": "s", "state": "running", "active_phase": "intent"}
    assert _answer_in_flight(base, {"waiting": False}, starting=False) is True
    assert _answer_in_flight(base, {"waiting": True}, starting=False) is False, 'an open question is waiting, not processing'
    assert _answer_in_flight(base, {"waiting": False}, starting=True) is False, 'the initial start has its own UI'
    assert _answer_in_flight({**base, "active_phase": "needs"}, {"waiting": False}, starting=False) is False
    assert _answer_in_flight(base, {"waiting": False, "decision": {"id": 1}}, starting=False) is False
    assert _answer_in_flight(base, {"waiting": False, "requirement": {"id": 1}}, starting=False) is False
    assert _answer_in_flight({**base, "state": "done"}, {"waiting": False}, starting=False) is False
    assert _answer_in_flight({**base, "engine_session_id": None}, {"waiting": False}, starting=False) is False


def test_engine_timing_stage_maps_only_observed_records():
    from delivery_api.child_supervisor import engine_timing_stage
    sid='f01b0cd8-e1d9-4509-bbef-7485c4c8e8f4'
    assert engine_timing_stage(f'staging.ready session={sid}') == (sid,'engine.session','Engine starting','started')
    assert engine_timing_stage(f'saver.setup.begin session={sid}') == (sid,'engine.session','Engine loading session','started')
    assert engine_timing_stage(f'advance.turn.begin session={sid}') == (sid,'engine.turn','Engine working','started')
    assert engine_timing_stage(f'advance.turn.end session={sid} dur_ms=44076') == (sid,'engine.turn','Finishing','completed')
    assert engine_timing_stage(f'session.saved session={sid} seq=4 pages=0 checkpoint=yes dur_ms=894 outcome=published') == (sid,'engine.persist','Session saved','completed')
    assert engine_timing_stage(f'llm.turn.begin session={sid} stage=categorize provider=deepseek model=deepseek-chat attempt=1') == (sid,'engine.llm','Model call running','started')
    assert engine_timing_stage(f'llm.turn.end session={sid} stage=categorize provider=deepseek model=deepseek-chat attempt=1 dur_ms=17320 outcome=ok') == (sid,'engine.llm','Model call running','completed')
    assert engine_timing_stage(f'control_commit.enter session={sid} fenced=False') is None, 'unmapped records stay invisible'
    assert engine_timing_stage('arbitrary child stderr with session='+sid) is None
    assert engine_timing_stage('advance.turn.begin') is None, 'no session id, no attribution'


def test_engine_timing_sink_records_progress_for_the_owning_task(pg_workspace_factory, monkeypatch):
    from delivery_api import config
    from delivery_api.postgres_store import PostgresStore
    from delivery_api.child_interview import ChildInterviewClient
    from delivery_api.tenancy import tenant_context
    monkeypatch.setattr(config, 'CLOUD_TENANTS', True)
    with pg_workspace_factory() as workspace, tenant_context(workspace.scope):
        store = PostgresStore(workspace.dsn.replace('user=sandbox', 'user=delivery_app'))
        task_id = store.create_task('Timing sink task')['task_id']
        session_id = 'f01b0cd8-e1d9-4509-bbef-7485c4c8e8f4'
        store.set_engine_session(task_id, session_id)
        client = object.__new__(ChildInterviewClient)
        client.store = store
        client._record_engine_timing(session_id, 'engine.turn', 'Engine working', 'started')
        events = store.events(task_id)
        assert [e['message'] for e in events] == ['Engine working']
        assert events[0]['payload']['stage'] == 'engine.turn'
        assert events[0]['payload']['status'] == 'started'
        unknown = object.__new__(ChildInterviewClient)
        unknown.store = store
        unknown._record_engine_timing('00000000-0000-0000-0000-000000000000', 'engine.turn', 'Engine working', 'started')
        assert [e['message'] for e in store.events(task_id)] == ['Engine working'], 'no event for a session no task owns'


@pytest.mark.skipif(not shutil.which('node'), reason='node not installed')
def test_shadowless_render_anchors_on_the_durable_submit_event(tmp_path):
    out = _run(tmp_path, """
        globalThis.window={addEventListener(){}};
        globalThis.document={addEventListener(){},querySelectorAll:()=>[]};
        const {answerProcessingLabel,ANSWER_PENDING_LABEL}=await import('./progress.js');
        const anchor={id:10,kind:'answer_submitted',message:'Answer submitted; engine processing',ts:1500};
        const pre={id:9,kind:'progress',message:'Coverage',ts:1400};
        const post={id:12,kind:'progress',message:'coverage',ts:1600};
        console.log(JSON.stringify({
          noAnchor: answerProcessingLabel([pre,post],{phaseKey:'intent'}),
          anchored: answerProcessingLabel([anchor,pre,post],{phaseKey:'intent'}),
          staleOnly: answerProcessingLabel([anchor,pre],{phaseKey:'intent'}),
          shadowWins: answerProcessingLabel([anchor,pre,post],{phaseKey:'intent',sinceMs:1650000}),
        }));
        """)
    import json
    got = json.loads(out)
    assert got['noAnchor'] == 'Processing your answer…', 'no shadow and no anchor: the honest blanket'
    assert got['anchored'] == 'Processing your answer · coverage', 'a shadow-less re-render must anchor on the durable submit event'
    assert got['staleOnly'] == 'Processing your answer…', 'signals from before the anchor stay invisible'
    assert got['shadowWins'] == 'Processing your answer · coverage', 'the durable submit event orders by id; a tab clock never overrides it'


def test_submit_writes_a_durable_anchor_before_the_turn_end_event(tmp_path, monkeypatch, pg_scoped_workspace):
    """The shadow is tab-local; the submit anchor must be durable so a mid-flight
    re-render can order post-submit signals without it."""
    import asyncio
    from uuid import uuid4
    from vmodel_contracts.interactive import (EngineSnapshot, EventType, InputKind, InputRequest,
        InteractiveEvent, NextStep, ProcessStatus, SubmitInputResponse)
    import delivery_api.app as module
    from delivery_api.auth import Actor
    monkeypatch.setattr(module.config, 'CLOUD_TENANTS', False)
    session = 'anchor-' + str(uuid4())
    artifacts = tmp_path / 'artifacts'
    monkeypatch.setenv('VMODEL_ARTIFACTS_DIR', str(artifacts))
    store = pg_scoped_workspace.store
    tid = store.create_task('Anchor', 'brief')['task_id']
    store.set_engine_session(tid, session)
    store.update_phase(tid, 'intent', 'running', detail='Waiting for founder input')
    monkeypatch.setattr(module, 'store', store)
    pending = InputRequest(input_id=uuid4(), kind=InputKind.TEXT, prompt='Q8?', options=[])
    class Slow:
        def snapshot(self, sid):
            return EngineSnapshot(session_id=sid, task_id=tid, sequence=1, process_status=ProcessStatus.WAITING_INPUT, next_step=NextStep.WAIT_INPUT, open_input=pending)
        def submit(self, sid, response):
            return SubmitInputResponse(accepted_event=InteractiveEvent(event_id=uuid4(), session_id=session, task_id=tid, phase_id=1, sequence=2, event_type=EventType.INPUT_ACCEPTED, process_status=ProcessStatus.WAITING_INPUT, next_step=NextStep.WAIT_INPUT), snapshot=EngineSnapshot(session_id=session, task_id=tid, sequence=2, process_status=ProcessStatus.WAITING_INPUT, next_step=NextStep.WAIT_INPUT, open_input=InputRequest(input_id=uuid4(), kind=InputKind.TEXT, prompt='Q9?', options=[])))
    monkeypatch.setattr(module, 'engine_client', Slow())
    assert asyncio.run(module.answer_interview(tid, module.InterviewAnswer(answer='Because x', decision_id=str(pending.input_id)), Actor('test', 'Founder'))) == {'status': 'accepted'}
    events = store.events(tid)
    anchors = [e for e in events if e['kind'] == 'answer_submitted']
    assert len(anchors) == 1, 'exactly one durable submit anchor per accepted answer'
    assert anchors[0]['payload'].get('input_id') == str(pending.input_id)
    assert anchors[0]['phase_key'] == 'intent'
    turn_end = [e for e in events if e['kind'] == 'answer']
    assert turn_end and anchors[0]['id'] < turn_end[0]['id'], 'the anchor lands at submit, before the turn-end answer event'


@pytest.mark.skipif(not shutil.which('node'), reason='node not installed')
def test_the_answer_path_stage_events_become_the_in_flight_label(tmp_path):
    """The two server stage events (engine working, recording) surface as the label, newest wins, never a timer."""
    out = _run(tmp_path, """
        globalThis.window={addEventListener(){}};
        globalThis.document={addEventListener(){},querySelectorAll:()=>[]};
        const {answerProcessingLabel}=await import('./progress.js');
        const base=[{id:5,kind:'answer_submitted',message:'Answer submitted; engine processing',phase_key:'intent',ts:2000}];
        const working=[...base,{id:6,kind:'progress',message:'Interview engine is working on your answer',phase_key:null,ts:2000.5,payload:{stage:'answer.engine',status:'started',source:'delivery.answer'}}];
        const recording=[...working,{id:7,kind:'progress',message:'Recording your answer and preparing the next question',phase_key:null,ts:2080,payload:{stage:'answer.record',status:'started',source:'delivery.answer'}}];
        console.log(JSON.stringify([answerProcessingLabel(base,{phaseKey:'intent'}),answerProcessingLabel(working,{phaseKey:'intent'}),answerProcessingLabel(recording,{phaseKey:'intent'})]));
        """)
    import json
    assert json.loads(out) == ['Processing your answer…',
        'Processing your answer · Interview engine is working on your answer',
        'Processing your answer · Recording your answer and preparing the next question']


@pytest.mark.skipif(not shutil.which('node'), reason='node not installed')
def test_label_refines_whatever_the_clock_skew_between_tab_and_server(tmp_path):
    """The server clock can run behind the browser's (Docker VM drift). Ordering by timestamp then hid every
    stage event of the answer and the label stayed generic. Ordering is by event id: no clock agreement needed."""
    out = _run(tmp_path, """
        globalThis.window={addEventListener(){}};
        globalThis.document={addEventListener(){},querySelectorAll:()=>[]};
        const {answerProcessingLabel,activePhaseProgress,ANSWER_PENDING_LABEL}=await import('./progress.js');
        const tabNow=Date.now(), res={};
        for (const skew of [0,-30,-600,+600]) {
          const sv=Math.floor(tabNow/1000)+skew;
          const before={id:7,kind:'progress',message:'Earlier turn',phase_key:null,ts:sv-5};
          const after={id:9,kind:'progress',message:'engine.llm',phase_key:null,ts:sv+2};
          res['id'+skew]=answerProcessingLabel([before,after],{phaseKey:'intent',sinceMs:tabNow,sinceId:8});
          res['only_before'+skew]=answerProcessingLabel([before],{phaseKey:'intent',sinceMs:tabNow,sinceId:8});
          res['active'+skew]=activePhaseProgress([before,after],{phaseKey:'intent',mode:'engine_question',answerPending:true,answerSince:tabNow,answerSinceId:8});
        }
        console.log(JSON.stringify(res));
        """)
    import json
    got = json.loads(out)
    for skew in (0, -30, -600, 600):
        assert got[f'id{skew}'] == 'Processing your answer · engine.llm', skew
        assert got[f'active{skew}'] == 'Processing your answer · engine.llm', skew
        assert got[f'only_before{skew}'] == 'Processing your answer…', 'an event older than the submit never names the stage'


def test_submit_shadow_records_the_newest_event_id_it_had():
    text = _web('task.js')
    assert 'after_id:Math.max(0,...(t.events||[]).map(e=>Number(e.id||0)))' in text
    assert text.count('answerSinceId') >= 5, 'every render path that passes answerSince must pass the id floor'
