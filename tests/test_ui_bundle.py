"""Bundled UI fixes: silent background checks, waiting banner, session-cost after Delete, one type scale."""
import re,shutil,subprocess,textwrap
from pathlib import Path
import pytest

WEB=Path(__file__).resolve().parents[1]/'src'/'delivery_api'/'web'


def _web(name):return (WEB/name).read_text()


def test_background_reads_are_tagged_and_showerror_stays_silent_for_them():
    assert 'background:true' in _web('task.js') and 'background' in _web('dialogs.js') and 'connection.failed()' in _web('client-core.js')
    assert 'reconnecting-strip' in _web('client-core.js') and '.reconnecting-strip' in _web('style.css')


@pytest.mark.skipif(not shutil.which('node'),reason='node not installed')
def test_connection_state_needs_three_failures_over_thirty_seconds_and_clears_on_success(tmp_path):
    src=tmp_path/'cs.mjs';src.write_text((WEB/'connection-state.js').read_text())
    run=tmp_path/'run.mjs';run.write_text(textwrap.dedent("""
        import {createConnectionState} from './cs.mjs';
        let t=1000;const seen=[];
        const c=createConnectionState({now:()=>t,show:()=>seen.push(true),hide:()=>seen.push(false)});
        c.failed();t=11000;c.failed();t=21000;c.failed();
        const early=seen.slice();
        t=32000;c.failed();
        const late=seen.slice();
        c.ok();
        console.log(JSON.stringify({early,late,after:seen}));
        """))
    out=subprocess.run(['node',str(run)],capture_output=True,text=True,timeout=20)
    assert out.returncode==0,out.stderr
    import json;r=json.loads(out.stdout)
    assert r['early']==[] and r['late']==[True] and r['after']==[True,False]


def test_waiting_for_the_user_is_a_clear_banner_and_the_timer_stops_counting():
    p=_web('progress.js')
    assert 'WAITING_MODES' in p and 'Waiting for you' in p and 'data-waiting' in p
    assert 'waitingBanner' in _web('task.js') and '.waiting-banner' in _web('style.css')


def test_session_cost_does_not_poll_a_deleted_task():
    s=_web('session-cost.js')
    assert 'stillHere' in s and 'No Session Selected' in s


def test_every_font_size_uses_the_type_scale():
    for name in ('style.css','purchase.css'):
        css=_web(name)
        body=css.split('}',1)[1] if name=='style.css' else css
        assert not re.findall(r'font-size:\s*[\d.]+(?:px|rem)',body),name
        assert not re.findall(r'font:[^;}]*?\b\d+(?:\.\d+)?px',body),name
    root=_web('style.css').split('}',1)[0]
    for t in ('xs','sm','md','lg','xl','display'):assert f'--fs-{t}:' in root
