from pathlib import Path
import ast

D=Path(__file__).resolve().parents[1]/'scripts/docker'


def test_remote_apply_never_rebuilds_and_checks_before_stop():
    text=(D/'remote_image_apply.py').read_text();ast.parse(text)
    assert "'build'" not in text and 'git ' not in text
    assert text.index("pulled image differs")<text.index("'stop','--time'")
    assert "'--password-stdin'" in text and "TemporaryDirectory(prefix='requi-registry-')" in text
    assert "reference not in image.get('RepoDigests',[])" in text
    assert "requi.shared-runtime" in text and "rollback restart unconfirmed" in text


def test_owner_apply_uses_hidden_stdin_secret_not_ssh_arguments():
    text=(D/'owner_image_deploy.py').read_text();ast.parse(text)
    assert text.index('if not apply:return')<text.index('getpass.getpass')<text.index("subprocess.run(['ssh'")
    assert "input=json.dumps(plan)" in text
    assert 'StrictHostKeyChecking=yes' in text
    assert "plan['token']='';token=''" in text
