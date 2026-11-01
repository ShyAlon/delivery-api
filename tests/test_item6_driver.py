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
    assert text.count(r'Type \"yes\"') == 1
