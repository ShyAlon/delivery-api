"""The req runner: runs a stamped script without an age prompt, still refuses expired or edited ones."""
import hashlib
import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest

REQ = Path(__file__).resolve().parents[1] / "scripts" / "ops" / "req"
pytestmark = pytest.mark.skipif(shutil.which("bash") is None or shutil.which("shasum") is None, reason="needs bash and shasum")


def _script(directory, name, body="echo RAN-IT\n", expires="2999-01-01T00:00:00Z", hours_old=3):
    text = "#!/usr/bin/env bash\n" + body
    sha = hashlib.sha256(text.encode()).hexdigest()[:8]
    path = Path(directory) / f"{name}.txt"
    path.write_text(f"#!/usr/bin/env bash\n# REQUI-SCRIPT {name} {sha} expires={expires}  (run it with:  req)\n{body}")
    old = time.time() - hours_old * 3600
    os.utime(path, (old, old))
    return path


def _req(directory, *args):
    return subprocess.run(["bash", str(REQ), *args], capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=60,
                          env={**os.environ, "REQ_DOWNLOADS": str(directory)})


def test_the_age_prompt_is_gone_from_the_runner():
    assert "outdated" not in REQ.read_text() and "minutes ago" not in REQ.read_text()


def test_a_script_downloaded_hours_ago_runs_with_no_question(tmp_path):
    script = _script(tmp_path, "probe", hours_old=5)
    out = _req(tmp_path)
    assert out.returncode == 0 and "RAN-IT" in out.stdout, out.stdout + out.stderr
    assert not script.exists() and list((tmp_path / "requi-done").glob("probe-*.txt"))


def test_an_expired_script_is_archived_and_not_run(tmp_path):
    _script(tmp_path, "old", expires="2001-01-01T00:00:00Z")
    out = _req(tmp_path)
    assert "RAN-IT" not in out.stdout and "expired and archived" in out.stdout and out.returncode == 1
    assert list((tmp_path / "requi-done" / "expired").glob("old-*.txt"))


def test_an_edited_script_aborts_and_runs_nothing(tmp_path):
    path = _script(tmp_path, "edited")
    path.write_text(path.read_text() + "echo EXTRA\n")
    out = _req(tmp_path)
    assert out.returncode == 1 and "damaged or edited" in out.stdout and "RAN-IT" not in out.stdout and "EXTRA" not in out.stdout
    assert path.exists()


def test_a_failing_script_stays_so_req_can_be_run_again(tmp_path):
    path = _script(tmp_path, "bad", body="echo ran; exit 3\n")
    out = _req(tmp_path)
    assert out.returncode == 3 and path.exists()


def test_clean_keeps_the_newest_and_archives_the_other_pending_scripts(tmp_path):
    old = _script(tmp_path, "stale-one", hours_old=9)
    mid = _script(tmp_path, "stale-two", hours_old=6)
    new = _script(tmp_path, "fresh", hours_old=1)
    out = _req(tmp_path, "clean")
    assert out.returncode == 0 and "kept (newest): fresh" in out.stdout and "RAN-IT" not in out.stdout, out.stdout + out.stderr
    assert new.exists() and not old.exists() and not mid.exists()
    assert len(list((tmp_path / "requi-done" / "stale").glob("stale-*.txt"))) == 2


def test_clean_with_nothing_pending_says_so(tmp_path):
    out = _req(tmp_path, "clean")
    assert out.returncode == 0 and "no pending Requi.re script" in out.stdout


def test_owner_command_syncs_clean_main_and_preserves_arguments(tmp_path):
    root=tmp_path/'root';repo=root/'delivery-api';(repo/'.git').mkdir(parents=True)
    (repo/'scripts/ops').mkdir(parents=True)
    (repo/'scripts/ops/req-owner.sh').write_text('printf "DISPATCH:%s\\n" "$@"\n')
    bin=tmp_path/'bin';bin.mkdir()
    git=bin/'git';git.write_text('''#!/bin/sh
shift 2
case "$1" in
 branch) echo main;;
 status) :;;
 fetch) echo FETCH;;
 merge) echo FF-ONLY;;
 rev-parse) echo same;;
 *) exit 9;;
esac
''');git.chmod(0o755)
    out=subprocess.run(['bash',str(REQ),'force-stop','--apply'],capture_output=True,text=True,env={**os.environ,'PATH':str(bin)+':'+os.environ['PATH'],'REQUI_ROOT':str(root)})
    assert out.returncode==0,out.stdout+out.stderr
    assert 'FETCH' in out.stdout and 'FF-ONLY' in out.stdout
    assert 'DISPATCH:force-stop' in out.stdout and 'DISPATCH:--apply' in out.stdout


def test_owner_command_refuses_wrong_branch_before_fetch(tmp_path):
    root=tmp_path/'root';repo=root/'delivery-api';(repo/'.git').mkdir(parents=True)
    bin=tmp_path/'bin';bin.mkdir();git=bin/'git'
    git.write_text('#!/bin/sh\necho feature\n');git.chmod(0o755)
    out=subprocess.run(['bash',str(REQ),'policy'],capture_output=True,text=True,env={**os.environ,'PATH':str(bin)+':'+os.environ['PATH'],'REQUI_ROOT':str(root)})
    assert out.returncode==1 and 'switch the checkout to main' in out.stderr


@pytest.mark.parametrize('name,expected',[('migrate','apply-pg-cutover.py'),('stop-all','--dry-run')])
def test_owner_dispatch_is_dry_default_and_uses_repo_scripts(tmp_path,name,expected):
    root=tmp_path/'delivery-api';(root/'scripts/ops').mkdir(parents=True);(root/'scripts/owner').mkdir();(root/'.venv/bin').mkdir(parents=True)
    shutil.copy(REQ.parent/'req-owner.sh',root/'scripts/ops/req-owner.sh')
    shutil.copy(REQ.parent/'terminal-log.sh',root/'scripts/ops/terminal-log.sh')
    (root/'scripts/ops/venv_check.py').write_text('')
    for target in ('apply-pg-cutover.py','force-stop-all.py','stop-all.py'):(root/'scripts/owner'/target).write_text('')
    python=root/'.venv/bin/python';python.write_text('#!/bin/sh\nprintf "PY:%s\\n" "$@"\n');python.chmod(0o755)
    out=subprocess.run(['bash',str(root/'scripts/ops/req-owner.sh'),name],capture_output=True,text=True)
    assert out.returncode==0,out.stderr
    assert expected in out.stdout and '--apply' not in out.stdout


def test_free_preview_dry_does_not_invoke_preview(tmp_path):
    root=tmp_path/'delivery-api';(root/'scripts/ops').mkdir(parents=True);(root/'.venv/bin').mkdir(parents=True)
    shutil.copy(REQ.parent/'req-owner.sh',root/'scripts/ops/req-owner.sh')
    shutil.copy(REQ.parent/'terminal-log.sh',root/'scripts/ops/terminal-log.sh')
    (root/'scripts/ops/venv_check.py').write_text('')
    (root/'scripts/ops/preview-exec.sh').write_text('echo MUST-NOT-START;exit 9\n')
    python=root/'.venv/bin/python';python.write_text('#!/bin/sh\nexit 0\n');python.chmod(0o755)
    out=subprocess.run(['bash',str(root/'scripts/ops/req-owner.sh'),'preview-free'],capture_output=True,text=True)
    assert out.returncode==0 and 'DRY REVIEW ONLY' in out.stdout and 'MUST-NOT-START' not in out.stdout


def test_policy_dry_exits_before_ack_and_stop_dry_skips_settings_save():
    text=(REQ.parent/'confirmed-expense-policy.sh').read_text()
    assert text.index("REQUI_POLICY_DRY_RUN')=='1'")<text.index("print('Type exactly:")
    stop=(REQ.parents[1]/'owner/stop-all.py').read_text()
    assert 'if values!=before and not args.dry_run:' in stop
    assert stop.index('if args.dry_run:')<stop.index("input('Type \"CONTINUE\"")


def test_unknown_owner_like_command_never_runs_download(tmp_path):
    _script(tmp_path,'polciy')
    out=_req(tmp_path,'polciy')
    assert out.returncode==1 and 'RAN-IT' not in out.stdout
    assert 'never fall back' in out.stderr
    out=_req(tmp_path,'download','polciy')
    assert out.returncode==0 and 'RAN-IT' in out.stdout


@pytest.mark.parametrize('name',['policy','reset-preview','migrate','preflight'])
def test_reserved_owner_name_never_runs_same_named_download(tmp_path,name):
    script=_script(tmp_path,name)
    out=subprocess.run(['bash',str(REQ),name],capture_output=True,text=True,
                       env={**os.environ,'REQ_DOWNLOADS':str(tmp_path),'REQUI_ROOT':str(tmp_path/'missing')})
    assert out.returncode==1 and 'RAN-IT' not in out.stdout and script.exists()
    assert 'No owner step was run' in out.stderr


def test_download_requires_exactly_one_name(tmp_path):
    _script(tmp_path,'probe')
    for args in [('download',),('download','probe','--apply')]:
        out=_req(tmp_path,*args)
        assert out.returncode==1 and 'RAN-IT' not in out.stdout


def test_locks_rejects_apply(tmp_path):
    root=tmp_path/'delivery-api';(root/'scripts/ops').mkdir(parents=True);(root/'scripts/owner').mkdir();(root/'.venv/bin').mkdir(parents=True)
    shutil.copy(REQ.parent/'req-owner.sh',root/'scripts/ops/req-owner.sh')
    shutil.copy(REQ.parent/'terminal-log.sh',root/'scripts/ops/terminal-log.sh')
    (root/'scripts/ops/venv_check.py').write_text('')
    (root/'scripts/owner/locks.py').write_text('')
    python=root/'.venv/bin/python';python.write_text('#!/bin/sh\nexit 0\n');python.chmod(0o755)
    out=subprocess.run(['bash',str(root/'scripts/ops/req-owner.sh'),'locks','--apply'],capture_output=True,text=True)
    assert out.returncode==1 and 'read-only' in out.stderr


def test_acceptance_dispatches_the_repo_entry_with_flags_unchanged(tmp_path):
    root=tmp_path/'delivery-api';(root/'scripts/ops').mkdir(parents=True)
    shutil.copy(REQ.parent/'req-owner.sh',root/'scripts/ops/req-owner.sh')
    shutil.copy(REQ.parent/'terminal-log.sh',root/'scripts/ops/terminal-log.sh')
    (root/'scripts/ops/acceptance.sh').write_text('printf "ARG:%s\\n" "$@"\n')
    out=subprocess.run(['bash',str(root/'scripts/ops/req-owner.sh'),'acceptance','--dry-run','--base-url','http://127.0.0.1:8123'],capture_output=True,text=True)
    assert out.returncode==0,out.stderr
    assert 'ARG:--dry-run' in out.stdout and 'ARG:http://127.0.0.1:8123' in out.stdout
