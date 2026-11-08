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
    run.write_text(textwrap.dedent(body))
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
    assert 'last engine signal' not in got['waiting'], 'signal age while waiting masquerades as a stall'
    assert 'last engine signal' in got['working']


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
