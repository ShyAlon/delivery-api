"""req debug (scripts/ops/preview-debug.py): ONE ZIP with every log that
matters - run-dir evidence, docker, host - scrubbed, bounded, read-only.

2026-11-09: gathering last night's evidence meant three manual pastes
(browser-smoke.log path, docker logs, state.json). The bundle exists so the
answer to "send me the logs" is one command whose last line is the ZIP path.
"""
import json,runpy,sys,zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest


def _fake_subprocess_run(command, **kwargs):
    if command[:2] == ['git', '-C']:
        if '--abbrev-ref' in command: return SimpleNamespace(stdout='main\n')
        if 'rev-parse' in command: return SimpleNamespace(stdout='fakehead-' + command[2].split('/')[-1] + '\n')
        return SimpleNamespace(stdout=' M dirty.txt\n')
    if command[:2] == ['docker', 'ps']:
        return SimpleNamespace(stdout='requi-preview-exec\nrequi-preview-exec-b3\nunrelated\n')
    if command[:2] == ['docker', 'exec']:
        assert command[3:5]==['python','-c']
        if command[5]=='from delivery_api.answer_diagnostics import exported_json; print(exported_json())':
            return SimpleNamespace(stdout='{"kind":"diagnostic-only-not-execution-replay","records":[]}')
        assert command[5]=='from delivery_api.answer_trace import exported_lines; print(exported_lines())'
        return SimpleNamespace(stdout='answer.trace {"total_syncs":199,"source":"child"}\n')
    if command[:2] == ['docker', 'logs']:
        return SimpleNamespace(stdout='postgresql://private:secret@db/x\nBearer secret123456\nAPI_KEY=another-secret\n')
    if command[:2] == ['docker', 'inspect']:
        # The format string pre-filters to safe fields; Env must never be requested.
        assert 'Env' not in str(command) and 'Mounts' not in str(command)
        return SimpleNamespace(stdout='{"name":"requi-preview-exec","image":"requi:x"}\n')
    if command[:2] == ['docker', 'images']:
        return SimpleNamespace(stdout='requi:latest abc123 2 hours ago\n')
    if command[:2] == ['docker', 'version']:
        return SimpleNamespace(stdout='27.3.1\n')
    if command[0] == 'uname':
        return SimpleNamespace(stdout='Darwin shys-mac 25.0.0\n')
    if command[0] == 'sw_vers':
        return SimpleNamespace(stdout='ProductName: macOS\n')
    raise AssertionError('unexpected command: ' + str(command))


@pytest.fixture
def owner_machine(tmp_path, monkeypatch):
    """A fake owner machine: home with run dirs + rebuild state, a repos root,
    and subprocess answering git/docker/host commands."""
    home = tmp_path / 'home'; home.mkdir()
    runs = home / '.local/share/requi/image-workflow'
    newest = runs / '413a76bd7770f2da551e65a7'; newest.mkdir(parents=True)
    (newest / 'state.json').write_text('{"stages": {}, "token": "sk-live-runsecret"}')
    (newest / 'browser-smoke.log').write_text('browser errors during boot:\n  request failed: POST http://127.0.0.1:8001/api/client-log - net::ERR_ABORTED\n')
    older = runs / '000000000000000000000000'; older.mkdir()
    (older / 'state.json').write_text('{"stages": {"old": true}}')
    (older / 'build.log').write_text('old build log\n')
    import os, time
    now = time.time()
    os.utime(older / 'state.json', (now - 100, now - 100))  # newest wins on state.json mtime
    rebuild = home / '.local/share/requi/rebuild'; rebuild.mkdir(parents=True)
    (rebuild / 'state.json').write_text('{"stage": "preview", "dsn": "postgresql://u:p@h/db"}')
    repos = tmp_path / 'repos'
    for name in ('delivery-api', 'interview-engine'):
        (repos / name / '.git').mkdir(parents=True)
    (repos / 'not-a-repo').mkdir()
    monkeypatch.setattr(Path, 'home', classmethod(lambda cls: home))
    monkeypatch.setattr(sys, 'platform', 'darwin')  # the owner machine is a Mac; pin the sw_vers branch
    monkeypatch.setenv('REQUI_ROOT', str(repos))
    import subprocess
    monkeypatch.setattr(subprocess, 'run', _fake_subprocess_run)
    return home


def _run_bundle(monkeypatch, capsys, argv):
    monkeypatch.setattr(sys, 'argv', argv)
    runpy.run_path('scripts/ops/preview-debug.py', run_name='__main__')
    captured = capsys.readouterr()
    _run_bundle.captured = captured
    return Path(captured.out.split('Debug ZIP: ')[1].splitlines()[0])


def test_the_bundle_carries_run_dir_docker_and_host_evidence_scrubbed(owner_machine, monkeypatch, capsys):
    path = _run_bundle(monkeypatch, capsys, ['preview-debug.py', '--code', 'PIN', '--step', 'grounding'])
    try:
        with zipfile.ZipFile(path) as z:
            names = set(z.namelist())
            assert {'facts.json', 'host.txt', 'repos.txt', 'images.txt', 'containers.txt',
                    'rebuild-state.json',
                    'run/413a76bd7770f2da551e65a7/state.json',
                    'run/413a76bd7770f2da551e65a7/browser-smoke.log',
                    'docker-requi-preview-exec.log', 'docker-requi-preview-exec-b3.log'} <= names
            assert not any('unrelated' in n for n in names), 'only requi-preview-exec* containers are collected'
            assert json.loads(z.read('answer-diagnostics-requi-preview-exec.json'))['kind']=='diagnostic-only-not-execution-replay'
            assert b'199' in z.read('answer-traces.jsonl')
            assert b'child' in z.read('answer-traces.jsonl')
            facts = json.loads(z.read('facts.json'))
            assert facts['failure_code'] == 'PIN' and facts['run_dir'].endswith('413a76bd7770f2da551e65a7')
            assert facts['repos']['delivery-api']['head'] == 'fakehead-delivery-api'
            assert facts['repos']['interview-engine']['head'] == 'fakehead-interview-engine'
            assert facts['missing'] == [], facts['missing']
            # A bare count could not say WHAT made a clone dirty (2026-11-10: dirty=1 with nobody editing). Name the files.
            assert facts['repos']['delivery-api']['dirty'] == 1 and facts['repos']['delivery-api']['dirty_files'] == ['M dirty.txt']
            smoke = z.read('run/413a76bd7770f2da551e65a7/browser-smoke.log').decode()
            assert 'net::ERR_ABORTED' in smoke
            assert 'macOS' in z.read('host.txt').decode() and '27.3.1' in z.read('host.txt').decode()
            blob = b''.join(z.read(n) for n in names).decode()
            for secret in ('private:secret', 'secret123456', 'another-secret', 'sk-live-runsecret', 'u:p@h'):
                assert secret not in blob, 'secret leaked into the bundle: ' + secret
        assert path.stat().st_mode & 0o777 == 0o600
    finally:
        path.unlink()


def test_run_dir_names_an_older_run_explicitly(owner_machine, monkeypatch, capsys):
    path = _run_bundle(monkeypatch, capsys, ['preview-debug.py', '--run-dir', '000000000000000000000000'])
    try:
        with zipfile.ZipFile(path) as z:
            assert 'run/000000000000000000000000/build.log' in z.namelist()
            assert not any(n.startswith('run/413a') for n in z.namelist())
    finally:
        path.unlink()


def test_missing_sources_are_named_and_the_zip_still_ships(owner_machine, monkeypatch, capsys):
    import subprocess
    def no_docker(command, **kwargs):
        if command[0] == 'docker': raise FileNotFoundError('docker')
        return _fake_subprocess_run(command, **kwargs)
    monkeypatch.setattr(subprocess, 'run', no_docker)
    (owner_machine / '.local/share/requi/rebuild/state.json').unlink()
    path = _run_bundle(monkeypatch, capsys, ['preview-debug.py'])
    try:
        with zipfile.ZipFile(path) as z:
            facts = json.loads(z.read('facts.json'))
            assert any('rebuild state' in m for m in facts['missing'])
            assert any('containers' in m for m in facts['missing'])
            assert 'run/413a76bd7770f2da551e65a7/state.json' in z.namelist(), 'what exists still ships'
    finally:
        path.unlink()
    assert 'MISSING:' in _run_bundle.captured.err


def test_failed_trace_collection_is_not_reported_as_complete(owner_machine,monkeypatch,capsys):
    import subprocess
    def failed(command,**kwargs):
        if command[:2]==['docker','exec']:
            return SimpleNamespace(stdout='trace exporter unavailable\n',returncode=1)
        return _fake_subprocess_run(command,**kwargs)
    monkeypatch.setattr(subprocess,'run',failed)
    path=_run_bundle(monkeypatch,capsys,['preview-debug.py'])
    try:
        with zipfile.ZipFile(path) as z:
            facts=json.loads(z.read('facts.json'))
            assert any('answer traces' in item for item in facts['missing'])
            assert 'unavailable' in z.read('answer-traces.jsonl').decode()
    finally:path.unlink()


def test_bundle_carries_real_diagnostic_recovery_without_consuming_it(owner_machine,monkeypatch,capsys,tmp_path):
    from interview_engine.document_diagnostics import answer_batch,bind,append
    from delivery_api.answer_diagnostics import exported_json
    import subprocess
    artifacts=tmp_path/'artifacts';monkeypatch.setenv('VMODEL_ARTIFACTS_DIR',str(artifacts))
    root=artifacts/'tenants/owner/sessions/.diagnostic-recovery'/('a'*64)
    class Docs:
        def diagnostic_many(self,ops):raise RuntimeError('test unavailable')
    with answer_batch(root,{'owner_id':'owner','operation_id':'fake'}):
        with bind(Docs(),tmp_path/'session'):
            append(tmp_path/'session/interview/run.log','API_KEY=test-only-secret\nproject evidence\n')
    evidence=next(root.glob('*.json'));before=evidence.read_bytes()
    def real_export(command,**kwargs):
        if command[:2]==['docker','exec'] and 'answer_diagnostics' in command[5]:
            return SimpleNamespace(stdout=exported_json(),returncode=0)
        return _fake_subprocess_run(command,**kwargs)
    monkeypatch.setattr(subprocess,'run',real_export)
    path=_run_bundle(monkeypatch,capsys,['preview-debug.py'])
    try:
        with zipfile.ZipFile(path) as z:
            raw=z.read('answer-diagnostics-requi-preview-exec.json')
            assert b'project evidence' in raw and b'test-only-secret' not in raw
            assert json.loads(raw)['kind']=='diagnostic-only-not-execution-replay'
        assert evidence.read_bytes()==before
    finally:path.unlink()


def test_run_evidence_allowlist_never_opens_settings_or_unknown_files(owner_machine,monkeypatch,capsys):
    run=owner_machine/'.local/share/requi/image-workflow/413a76bd7770f2da551e65a7'
    for name in ('preview-container.env','service.env','credentials.json','mystery.txt'):
        (run/name).write_text('INTERVIEW_ENGINE_TOKEN=FAKE-DO-NOT-EXPORT-1234')
    original=Path.open
    def guarded(path,*args,**kwargs):
        if path.parent==run and path.name in ('preview-container.env','service.env','credentials.json','mystery.txt'):
            pytest.fail('collector must never open '+path.name)
        return original(path,*args,**kwargs)
    monkeypatch.setattr(Path,'open',guarded)
    path=_run_bundle(monkeypatch,capsys,['preview-debug.py'])
    with zipfile.ZipFile(path) as z:
        assert not any(name.endswith(('.env','credentials.json','mystery.txt')) for name in z.namelist())
    path.unlink()


def test_allowlisted_symlink_is_not_followed(owner_machine,monkeypatch,capsys):
    run=owner_machine/'.local/share/requi/image-workflow/413a76bd7770f2da551e65a7'
    secret=owner_machine/'private.env';secret.write_text('INTERVIEW_ENGINE_TOKEN=FAKE-NO-EXPORT-1234')
    (run/'answer-sync-table.txt').symlink_to(secret)
    path=_run_bundle(monkeypatch,capsys,['preview-debug.py'])
    with zipfile.ZipFile(path) as z:
        assert not any(name.endswith('answer-sync-table.txt') for name in z.namelist())
        assert b'FAKE-NO-EXPORT-1234' not in b''.join(z.read(name) for name in z.namelist())
    path.unlink()
