from pathlib import Path
import subprocess
ROOT=Path(__file__).parents[1]


def test_bootstrap_default_is_dry_before_root_package_firewall_or_key_checks():
    result=subprocess.run(['bash',str(ROOT/'scripts/bootstrap-server.sh')],capture_output=True,text=True,check=True)
    assert result.stdout.startswith('DRY:') and 'No host changes' in result.stdout
    text=(ROOT/'scripts/bootstrap-server.sh').read_text()
    assert text.index('if [ "${1:-}" != --execute ]')<text.index('apt-get update')
    assert '/root/.ssh/authorized_keys' not in text
    assert 'REQUI_DEPLOY_PUBLIC_KEY_FILE' in text and 'refusing to replace or widen access' in text
    subprocess.run(['bash','-n',str(ROOT/'scripts/bootstrap-server.sh')],check=True)
