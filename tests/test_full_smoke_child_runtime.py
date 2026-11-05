"""The image diagnostic follows the real delivery-parent runtime, not a retired daemon."""
import ast
from pathlib import Path


def test_full_smoke_tracks_delivery_parent_and_health():
    text=(Path(__file__).resolve().parents[1]/'scripts/docker/full-smoke.py').read_text()
    ast.parse(text)
    assert "args[1:3]==[b'-m',b'delivery_api']" in text
    assert "killed==1" in text
    assert "delivery_death_stops_container" in text
    assert "engine_death_stops_container" not in text
    assert "8010/v1" not in text
    assert "'--healthcheck'" in text
    assert "explicit disposable DELIVERY_TEST_POSTGRES_URL required" in text
