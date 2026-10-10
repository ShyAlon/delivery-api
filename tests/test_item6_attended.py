import pytest
from attended import Attended

def test_stale_digest_stops_before_gate_decision(tmp_path,monkeypatch):
 monkeypatch.setattr('builtins.input',lambda prompt:'wrong')
 with pytest.raises(ValueError,match='exact'):Attended(tmp_path,lambda:'owner').gate({'gate':'srs'},'a'*64)
 assert (tmp_path/'review-001.json').stat().st_mode&0o777==0o600

def test_fresh_review_requires_explicit_cost_ack(tmp_path,monkeypatch):
 responses=iter(['a'*64,'no'])
 monkeypatch.setattr('builtins.input',lambda prompt:next(responses))
 with pytest.raises(ValueError,match='approval'):Attended(tmp_path,lambda:'owner').approve_revision({'review':{},'review_sha256':'a'*64})
