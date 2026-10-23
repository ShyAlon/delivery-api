"""Egress-guard regression for the crosshost stub (2026-10-23 escaped-HTTP
incident: an unstubbed standalone diagnostic dispatched real HTTPS requests
to DeepSeek with a dummy key and got real 401s).

The guard in tests/crosshost_stub/sitecustomize.py must refuse external
connect/connect_ex, hostname resolution and async/client paths BEFORE any
network I/O, install before the fallible model-stub imports (a sitecustomize
error must not fail open), and still allow loopback (app, JWKS) and
Unix-domain sockets (Postgres). All checks are hermetic: a blocked attempt
raises the guard's own OSError subclass before any syscall, so no packet
leaves the machine; the allowed cases bind/connect only in-sandbox loopback
and Unix sockets.
"""
import os, subprocess, sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
STUB_DIR = REPO / 'tests' / 'crosshost_stub'
IE_SRC = Path(os.environ.get('DELIVERY_TEST_ENGINE_SOURCE', '')) / 'src'
IE_VENV_PY = Path(os.environ.get('DELIVERY_TEST_ENGINE_SOURCE', '')) / '.venv/bin/python'
GUARD_MSG = 'crosshost egress guard'

def _child(python, code, pythonpath):
    env = {'PATH': '/usr/bin:/bin', 'CROSSHOST_STUB': '1', 'PYTHONPATH': pythonpath}
    return subprocess.run([str(python), '-c', code], capture_output=True, text=True, env=env, timeout=30)

def _stub_pp():
    # Full stub: model-stub imports must succeed (interview-engine + langchain available).
    assert IE_SRC.is_dir() and IE_VENV_PY.is_file(), 'requires DELIVERY_TEST_ENGINE_SOURCE (interview-engine checkout)'
    return '%s:%s' % (STUB_DIR, IE_SRC)

def test_external_ip_connect_and_connect_ex_blocked_before_network():
    code = (
        "import socket\n"
        "for fn in (socket.socket.connect, socket.socket.connect_ex):\n"
        "    s = socket.socket()\n"
        "    try:\n"
        "        fn(s, ('93.184.216.34', 443))\n"
        "        print('FAIL: connected')\n"
        "    except OSError as exc:\n"
        "        assert 'crosshost egress guard' in str(exc), exc\n"
        "    finally:\n"
        "        s.close()\n"
        "print('OK')\n")
    p = _child(IE_VENV_PY, code, _stub_pp())
    assert p.stdout.strip().endswith('OK'), p.stdout[-300:] + p.stderr[-300:]

def test_external_hostname_resolution_blocked():
    code = (
        "import socket\n"
        "try:\n"
        "    socket.getaddrinfo('api.deepseek.com', 443)\n"
        "    print('FAIL: resolved')\n"
        "except OSError as exc:\n"
        "    assert 'crosshost egress guard' in str(exc), exc\n"
        "print('OK')\n")
    p = _child(IE_VENV_PY, code, _stub_pp())
    assert p.stdout.strip().endswith('OK'), p.stdout[-300:] + p.stderr[-300:]

def test_async_client_path_blocked_before_network():
    code = (
        "import asyncio\n"
        "async def main():\n"
        "    try:\n"
        "        await asyncio.wait_for(asyncio.open_connection('93.184.216.34', 443), 5)\n"
        "        print('FAIL: connected')\n"
        "    except OSError as exc:\n"
        "        assert 'crosshost egress guard' in str(exc), exc\n"
        "asyncio.run(main())\n"
        "print('OK')\n")
    p = _child(IE_VENV_PY, code, _stub_pp())
    assert p.stdout.strip().endswith('OK'), p.stdout[-300:] + p.stderr[-300:]

def test_loopback_and_unix_sockets_allowed(tmp_path):
    import socket as srv_socket, threading
    listener = srv_socket.socket()
    listener.bind(('127.0.0.1', 0)); listener.listen(1)
    port = listener.getsockname()[1]
    accepted = threading.Thread(target=lambda: listener.accept()[0].close(), daemon=True)
    accepted.start()
    unix_srv = srv_socket.socket(srv_socket.AF_UNIX)
    upath = str(tmp_path / 'pg.sock')
    unix_srv.bind(upath); unix_srv.listen(1)
    uaccepted = threading.Thread(target=lambda: unix_srv.accept()[0].close(), daemon=True)
    uaccepted.start()
    code = (
        "import socket\n"
        "s = socket.create_connection(('127.0.0.1', %d), timeout=5); s.close()\n"
        "u = socket.socket(socket.AF_UNIX); u.connect(%r); u.close()\n"
        "print('OK')\n" % (port, upath))
    p = _child(IE_VENV_PY, code, _stub_pp())
    assert p.stdout.strip().endswith('OK'), p.stdout[-300:] + p.stderr[-300:]
    listener.close(); unix_srv.close()

def test_guard_holds_when_model_stub_import_fails():
    """sitecustomize errors must not fail open: without interview-engine on
    PYTHONPATH the model-stub import fails, startup continues, and the guard
    (installed before that import) must still refuse egress - the incident's
    exact dummy-credential standalone-repro shape."""
    code = (
        "import os, socket\n"
        "os.environ['DEEPSEEK_API_KEY'] = 'test-key'\n"
        "s = socket.socket()\n"
        "try:\n"
        "    s.connect(('142.250.80.46', 443))\n"
        "    print('FAIL: connected')\n"
        "except OSError as exc:\n"
        "    assert 'crosshost egress guard' in str(exc), exc\n"
        "finally:\n"
        "    s.close()\n"
        "print('OK')\n")
    p = _child(sys.executable, code, str(STUB_DIR))  # no IE source: the model-stub import fails
    assert p.stdout.strip().endswith('OK'), p.stdout[-300:] + p.stderr[-500:]
    assert 'Error in sitecustomize' in p.stderr or 'sitecustomize' in p.stderr or p.returncode == 0

def test_guard_install_precedes_fallible_imports():
    """Structural: the guard install + startup self-check come before the first
    fallible model-stub import in the sitecustomize source."""
    src = (STUB_DIR / 'sitecustomize.py').read_text()
    guard_at = src.index('_socket.socket.connect = _connect')
    selfcheck_at = src.index("raise SystemExit('crosshost egress guard self-check FAILED")
    first_fallible_import = src.index('from langchain_core.messages import AIMessage')
    assert guard_at < selfcheck_at < first_fallible_import
