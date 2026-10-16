import subprocess
from pathlib import Path

DRIVER = Path(__file__).resolve().parents[1] / 'scripts' / 'item6' / 'vps' / 'item6-run.sh'


def test_driver_is_valid_bash():
    subprocess.run(['bash', '-n', str(DRIVER)], check=True)


def test_driver_keeps_the_money_and_run_guards():
    text = DRIVER.read_text()
    assert 'GO $RID CEILING $CEILING' in text
    assert 'CEILING=2.00' in text
    assert 'evidence is not empty' in text
    assert 'docker rm requi-item6-$RID' in text and text.count('docker rm ') == 1
    assert text.count('Type yes') == 1


RESUME = DRIVER.parent / 'item6-resume.sh'


def test_resume_driver_is_valid_bash_and_keeps_guards():
    subprocess.run(['bash', '-n', str(RESUME)], check=True)
    text = RESUME.read_text()
    assert 'GO $RID CEILING $CEILING' in text and 'CEILING=2.00' in text
    assert 'evidence-pre-resume-$N' in text and '[ ! -e evidence-pre-resume-$N ]' in text
    assert 'item6-database-resume-$N.dump' in text
    assert 'cp -a evidence evidence-pre-resume-$N' in text
    assert 'item6-topup-"+os.environ["RID"]+"-01' in text
    assert 'docker rm' not in text
    assert 'ITEM6_RUN_LIB=1' in text


def test_run_driver_can_be_sourced_without_running():
    assert 'ITEM6_RUN_LIB' in DRIVER.read_text()


def test_resume_checks_the_compiled_config_on_a_copy_without_evidence_and_shows_the_real_message():
    text = RESUME.read_text()
    assert "! -name 'evidence*'" in text
    assert 'verifier_msg' in text and 'verify-*.py . >/dev/null' not in text


def test_resume_verifier_copy_has_an_empty_private_evidence_dir_and_prints_the_last_error_line():
    text = RESUME.read_text()
    assert 'mkdir -m 700 $VT/evidence' in text
    assert text.index('mkdir -m 700 $VT/evidence') < text.index('python3 private/verify-*.py')
    assert "tail -2 | tr" in text
