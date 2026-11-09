"""The child stop/auth/generation watch cadence is a named constant, never a bare 1s poll.

2026-11-09 production evidence (turns 8/9/10, answer POST db_queries=396): the 1Hz
watch re-read task + session binding + auth session every second of the child's
~100s life - ~300 of the 396 queries (75%). The cadence constant keeps stop
reaction within one graceful-stop window without per-second DB reads. Static
invariant: the real-child budget suite is environment-broken in the sandbox
(same class as the six known real-child/fresh-start failures), so the cadence
is pinned here instead of by statement counting."""
import re
from pathlib import Path

SRC=Path(__file__).resolve().parents[1]/'src/delivery_api/child_interview.py'

def _watch_loop(text):
    start=text.index('async def watch')
    end=text.index('def fence_stop')
    return text[start:end]

def test_stop_watch_cadence_is_a_bounded_constant():
    text=SRC.read_text()
    match=re.search(r"^_STOP_WATCH_SECONDS\s*=\s*([0-9.]+)",text,re.M)
    assert match,'child stop-watch cadence must be a named module constant'
    seconds=float(match.group(1))
    assert 2.0<=seconds<=10.0,seconds

def test_stop_watch_sleeps_on_the_constant():
    text=SRC.read_text()
    loop=_watch_loop(text)
    assert 'asyncio.sleep(_STOP_WATCH_SECONDS)' in loop,'watch loop must sleep on _STOP_WATCH_SECONDS'
    assert not re.search(r"asyncio\.sleep\(\s*1\s*\.?\s*0?\s*\)",loop),'bare 1s poll is the 2026-11-09 query storm'
