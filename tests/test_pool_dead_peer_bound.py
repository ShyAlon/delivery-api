"""A dead network peer must fail a pooled connection in seconds, not a minute.

2026-11-10: one progress-stream read took 59,405 ms on an idle app (stream.events.slow), the other 32 took ~0.8 s.
59.4 s is the pool's own keepalive budget (30 s idle + 3 x 10 s): a connection whose peer silently vanished (laptop VM,
NAT or pooler idle drop) hangs the next check or statement until the kernel gives up. TCP_USER_TIMEOUT bounds
unacknowledged data directly, and tighter keepalives cover the idle case."""
import os
import pytest

from delivery_api import db_pool

pytestmark = pytest.mark.skipif(not os.environ.get('DELIVERY_TEST_POSTGRES_DSN'), reason='requires isolated migrated Postgres')


def test_dead_peer_is_detected_within_twenty_seconds():
    dsn = os.environ['DELIVERY_TEST_POSTGRES_DSN'].replace('user=sandbox', 'user=delivery_app')
    kw = db_pool.pool_for(dsn).kwargs
    assert 0 < int(kw['tcp_user_timeout']) <= 15000, kw
    assert int(kw['keepalives_idle']) + int(kw['keepalives_interval']) * int(kw['keepalives_count']) <= 20, kw
