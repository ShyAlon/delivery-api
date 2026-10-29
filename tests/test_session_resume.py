"""Coming back to a long-idle tab counts as activity, so the first refresh renews the session instead of getting a 401."""
import re
from pathlib import Path

WEB=Path(__file__).resolve().parents[1]/'src'/'delivery_api'/'web'


def _web(name):return (WEB/name).read_text()


def test_client_core_lets_a_resume_count_as_activity():
    core=_web('client-core.js')
    assert 'export function noteActivity(){lastActivity=Date.now()}' in core
    # The idle rule itself is unchanged: renewal is still refused after 20 minutes without activity.
    assert 'Date.now()-lastActivity>20*60*1000)return' in core


def test_both_visibility_handlers_note_activity_before_they_refresh():
    s=_web('session-cost.js')
    assert "import {api,noteActivity} from './client-core.js'" in s
    handlers=re.findall(r'const visible=\(\)=>\{[^;]*;?[^}]*\}\};',s)
    assert len(handlers)==2
    assert "const visible=()=>{if(!document.hidden){noteActivity();balance.refresh()}};" in handlers
    assert "const visible=()=>{if(!document.hidden){noteActivity();refreshAll()}};" in handlers
    for h in handlers:
        assert h.index('noteActivity()')<h.index('refresh')


def test_the_background_timer_is_not_activity_so_a_tab_left_open_and_idle_still_expires():
    s=_web('session-cost.js')
    timers=re.findall(r'setInterval\(\(\)=>\{[^}]*\},60000\)',s)
    assert len(timers)==2 and all('noteActivity' not in t for t in timers)
