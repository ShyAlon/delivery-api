"""Terminal confirmation replaces hash copying, never execution approval."""
import io,json
import pytest
from test_item6_single_config import filled
from single_config import compile_config


def test_review_yes_records_exact_receipt_and_keeps_launch_false(tmp_path):
    from single_config import attest_config
    root=filled(tmp_path);out=io.StringIO();prompts=[]
    def answer(prompt):prompts.append(prompt);return 'yes'
    result=attest_config(root,prompt=answer,output=out)
    assert result['reviewed'] and result['launch_authorized'] is False and result['run_started'] is False
    assert len(prompts)==1 and 'config' in prompts[0].lower()
    text=out.getvalue();assert 'nonsecret_values' in text and 'review_conditions' in text
    assert 'fixture-key' not in text and 'fixture-secret' not in text
    assert compile_config(root)['launch_authorized'] is False


@pytest.mark.parametrize('answer',['no','','y','YES PLEASE'])
def test_non_yes_never_writes_receipt(tmp_path,answer):
    from single_config import attest_config
    root=filled(tmp_path);out=io.StringIO()
    result=attest_config(root,prompt=lambda _:answer,output=out)
    assert result['reviewed'] is False and not list((root/'private').glob('review-*.json'))
    with pytest.raises((ValueError,FileNotFoundError)):compile_config(root)


def test_secret_only_edit_during_prompt_refuses_unseen_source(tmp_path):
    from single_config import attest_config
    root=filled(tmp_path)
    def edit(_):
        p=root/'private/.env';p.write_text(p.read_text().replace('fixture-key','changed-key'));return 'yes'
    with pytest.raises(ValueError,match='changed'):attest_config(root,prompt=edit,output=io.StringIO())
    assert not list((root/'private').glob('review-*.json'))


def test_eof_never_writes_receipt(tmp_path):
    from single_config import attest_config
    root=filled(tmp_path)
    def eof(_):raise EOFError
    result=attest_config(root,prompt=eof,output=io.StringIO())
    assert result['reviewed'] is False and not list((root/'private').glob('review-*.json'))


def test_cli_noninteractive_is_read_only(tmp_path):
    import subprocess,sys
    from pathlib import Path
    root=filled(tmp_path)
    cli=Path(__file__).parents[1]/'scripts/item6/scaffold.py'
    result=subprocess.run([sys.executable,str(cli),'review-config',str(root)],input='yes\n',text=True,capture_output=True)
    assert result.returncode==0 and json.loads(result.stdout)['reviewed'] is False
    assert not list((root/'private').glob('review-*.json'))


def test_cli_terminal_displays_then_confirms_without_hash(tmp_path):
    import os,pty,select,subprocess,sys,time
    from pathlib import Path
    root=filled(tmp_path);master,slave=pty.openpty()
    cli=Path(__file__).parents[1]/'scripts/item6/scaffold.py'
    process=subprocess.Popen([sys.executable,str(cli),'review-config',str(root)],stdin=slave,stdout=slave,stderr=slave)
    os.close(slave);text=b'';answered=False;deadline=time.monotonic()+10
    try:
        while time.monotonic()<deadline:
            ready,_,_=select.select([master],[],[],0.2)
            if ready:
                try:chunk=os.read(master,65536)
                except OSError:break
                if not chunk:break
                text+=chunk
                if b'Type yes' in text and not answered:
                    assert b'nonsecret_values' in text and b'review_conditions' in text
                    os.write(master,b'yes\n');answered=True
            if process.poll() is not None and not ready:break
        assert process.wait(timeout=2)==0
    finally:
        if process.poll() is None:process.kill();process.wait()
        os.close(master)
    assert answered and b'fixture-key' not in text and b'fixture-secret' not in text
    assert b'"reviewed": true' in text and len(list((root/'private').glob('review-*.json')))==1
    assert not list(root.glob('run-compiled-*.sh'))


def test_run_identity_edit_during_prompt_refuses(tmp_path):
    from single_config import attest_config
    root=filled(tmp_path)
    def edit(_):
        p=root/'run-id.json';d=json.loads(p.read_text());d['run_id']='a'*32;p.write_text(json.dumps(d));return 'yes'
    with pytest.raises(ValueError,match='changed'):attest_config(root,prompt=edit,output=io.StringIO())
    assert not list((root/'private').glob('review-*.json'))
