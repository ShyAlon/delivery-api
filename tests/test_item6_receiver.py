import json, sys, threading, http.client
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).parents[1]/'scripts/item6'))
from receiver.receiver import *

RAW=b'{ "id" : "WH-test-1", "event_type" : "PAYMENT.CAPTURE.COMPLETED", "resource": {"value":"sensitive"} }\n'
H=[(k,'private-signed-value') for k in HEADERS]

def test_exact_raw_and_private_export(tmp_path):
    tmp_path.chmod(0o700);receipt=store(tmp_path,RAW,H);f=tmp_path/(receipt+'.json')
    assert f.stat().st_mode&0o077==0
    envelope=json.loads(f.read_bytes());raw,headers=decode_envelope(envelope)
    assert raw==RAW and headers==dict(H) and envelope['verified'] is False
    envelope['raw_sha256']='0'*64
    with pytest.raises(EvidenceError):decode_envelope(envelope)

@pytest.mark.parametrize('headers',[H[:-1],H+H[:1],[(k,'bad\nvalue') for k in HEADERS]])
def test_header_failure_writes_nothing(tmp_path,headers):
    tmp_path.chmod(0o700)
    with pytest.raises(EvidenceError):store(tmp_path,RAW,headers)
    assert not list(tmp_path.iterdir())

@pytest.mark.parametrize('raw',[b'{}',b'{"id":"a","id":"b"}',RAW.replace(b'COMPLETED',b'REFUNDED'),b'NaN'])
def test_bad_event_writes_nothing(tmp_path,raw):
    tmp_path.chmod(0o700)
    with pytest.raises(EvidenceError):store(tmp_path,raw,H)
    assert not list(tmp_path.iterdir())

def test_spool_quota_and_symlink(tmp_path,monkeypatch):
    import receiver.receiver as m
    tmp_path.chmod(0o700);monkeypatch.setattr(m,'MAX_FILES',1);store(tmp_path,RAW,H)
    with pytest.raises(EvidenceError):store(tmp_path,RAW,H)
    (tmp_path/'symlink').symlink_to('/etc/passwd')
    with pytest.raises(EvidenceError):store(tmp_path,RAW,H)

def test_http_no_public_receipts_and_fixed_errors(tmp_path,capsys):
    tmp_path.chmod(0o700);server=Server(('127.0.0.1',0),tmp_path)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try:
        conn=http.client.HTTPConnection(*server.server_address)
        conn.request('POST',PATH,RAW,{'Content-Type':'application/json',**dict(H)})
        response=conn.getresponse();body=response.read();assert response.status==200 and b'sensitive' not in body
        conn=http.client.HTTPConnection(*server.server_address);conn.request('GET','/spool');response=conn.getresponse();assert response.status==404;response.read()
        conn=http.client.HTTPConnection(*server.server_address);conn.request('POST',PATH,RAW,{'Content-Type':'application/json'})
        response=conn.getresponse();assert response.status==400 and b'private-signed' not in response.read()
        assert not capsys.readouterr().err
    finally:server.shutdown();server.server_close()

def test_attended_raw_bytes(monkeypatch,tmp_path):
    from attended import Attended
    tmp_path.chmod(0o700);receipt=store(tmp_path,RAW,H);f=tmp_path/(receipt+'.json')
    monkeypatch.setenv('ITEM6_WEBHOOK_DIR',str(tmp_path/'no-export'));monkeypatch.setenv('ITEM6_WEBHOOK_WAIT_SECONDS','0')  # typed-path fallback
    monkeypatch.setattr('builtins.input',lambda _:str(f))
    raw,headers=Attended(tmp_path,lambda:'owner').webhook();assert raw==RAW and headers==dict(H)

def test_setup_dry_and_shell_safety():
    import subprocess
    setup=Path(__file__).parents[1]/'scripts/item6/receiver/setup.sh'
    out=subprocess.run(['bash',str(setup),'dry','8.8.8.8'],capture_output=True,text=True)
    assert out.returncode==0 and 'no changes' in out.stdout
    out=subprocess.run(['bash',str(setup),'dry','127.0.0.1'],capture_output=True,text=True)
    assert out.returncode!=0
    text=setup.read_text()
    assert 'curl --noproxy' in text and 'reload --force' in text
    assert 'certbot renew --cert-name requi-receiver-ip --dry-run' in text
    assert 'ufw reset' not in text and 'PasswordAuthentication no' in text
    for script in [setup,setup.with_name('mac-setup.sh')]:
        assert subprocess.run(['bash','-n',str(script)]).returncode==0

def test_acme_existing_private_intermediate_is_fixed_explicitly(tmp_path):
    import subprocess
    setup=Path(__file__).parents[1]/'scripts/item6/receiver/setup.sh'
    text=setup.read_text()
    marker=' for PUBLIC_DIR in /var/lib/requi-acme '
    start=text.index(marker);end=text.index('\n done',start)+len('\n done')
    root=tmp_path/'public-acme';root.mkdir(mode=0o755)
    middle=root/'.well-known';middle.mkdir(mode=0o700)
    private=tmp_path/'private-spool';private.mkdir(mode=0o700)
    script=text[start:end].replace('/var/lib/requi-acme',str(root)).replace('-o caddy -g caddy ','')
    out=subprocess.run(['bash','-c','umask 077; fail(){ exit 1; };'+script],capture_output=True)
    assert out.returncode==0
    for path in (root,middle,middle/'acme-challenge'):assert path.stat().st_mode&0o777==0o755
    assert private.stat().st_mode&0o777==0o700
    assert 'sudo -u caddy test -r' in text
