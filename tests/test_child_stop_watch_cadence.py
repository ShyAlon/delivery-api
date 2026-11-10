"""The child stop/auth/generation watch never polls at 1 s or 5 s (cadence contract).

2026-11-09 production evidence: a 1 Hz watch re-read task + session binding + auth session every second of the
child's ~100 s life (about 300 of 396 answer queries). 2026-11-10 trace: even at 5 s it was 21 cycles x 15 round
trips per answer. Contract now (owner ruling 2026-11-10): a Stop click wakes the watcher at once through the event bus,
and a safety read every 30 s covers revocation and a Stop made in another process. The behavior is tested in
tests/test_stop_watch_wake.py; this file pins that the child handler uses it and does not grow its own poll."""
import re
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / 'src/delivery_api'


def test_child_handler_uses_the_shared_stop_watch():
    text = (SRC / 'child_interview.py').read_text()
    assert 'watch_for_stop(' in text
    assert 'async def watch' not in text, 'the handler must not carry its own poll loop'
    assert not re.search(r'asyncio\.sleep\(_STOP_WATCH', text)


def test_stop_watch_safety_interval_is_thirty_seconds_and_never_sleeps_short():
    text = (SRC / 'stop_watch.py').read_text()
    assert re.search(r'^STOP_SAFETY_SECONDS = 30\.0$', text, re.M)
    assert 'asyncio.sleep' not in text, 'the watcher waits on the event bus, it does not poll'
