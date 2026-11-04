"""The retired inspection launcher delegates. Full launcher cases live in test_preview_exec_preflight."""
from pathlib import Path
from fastapi.testclient import TestClient
ROOT=Path(__file__).parents[1]
WEB=ROOT/'src/delivery_api/web'

def _guard_request(origin):
    from types import SimpleNamespace
    return SimpleNamespace(method="POST", headers={"origin": origin} if origin else {}, cookies={}, url=SimpleNamespace(path="/api/auth/signin"),
                           base_url="http://127.0.0.1:18765/")


def _guard(monkeypatch, public_origin, origin, alt=frozenset()):
    from delivery_api import auth, config
    monkeypatch.setattr(auth, "enabled", lambda: True)
    monkeypatch.setattr(config, "PUBLIC_ORIGIN", public_origin)
    monkeypatch.setattr(config, "ALT_ORIGINS", alt)
    monkeypatch.setattr(config, "CLOUD_TENANTS", False)
    try:
        auth.csrf_guard(_guard_request(origin))
        return None
    except Exception as e:  # HTTPException
        return e


def test_the_live_guard_is_unchanged_and_the_rejection_explains_itself(monkeypatch):
    live = "https://15-235-101-85.sslip.io"
    assert _guard(monkeypatch, live, live) is None
    err = _guard(monkeypatch, live, "http://127.0.0.1:18765")
    assert err.status_code == 403 and err.detail["error"] == "Cross-site request rejected."
    assert "different address" in err.detail["cause"] and "official address" in err.detail["next"]
    assert "audit" not in str(err.detail).lower() and "server log" not in str(err.detail).lower()


def test_exact_alt_origins_are_values_not_a_preview_privilege(monkeypatch):
    from delivery_api import config
    env={'DELIVERY_ALT_ORIGINS':'http://localhost:8000'}
    for extra in ({},{'DELIVERY_PREVIEW':'0'},{'DELIVERY_PREVIEW':'1'}):
        assert config.alt_origins(env|extra)=={'http://localhost:8000'}
    assert _guard(monkeypatch,'https://fixture.invalid','https://evil.invalid',config.alt_origins(env)).status_code==403

def test_retired_preview_configuration_does_not_change_auth_or_banner(monkeypatch):
    import delivery_api.app as module
    monkeypatch.setenv('DELIVERY_PREVIEW','1')
    assert 'preview' not in TestClient(module.app).get('/api/auth').json()
    assert 'previewBannerText' not in (WEB/'chrome.js').read_text()
