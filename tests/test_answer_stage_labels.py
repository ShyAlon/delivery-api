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
