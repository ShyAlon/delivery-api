import importlib.util
import json
from pathlib import Path
import ssl
import urllib.request
import pytest
import jwt

spec=importlib.util.spec_from_file_location('fixture_services',Path(__file__).resolve().parents[1]/'scripts/docker/fixture_services.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)


def test_fixture_jwt_uses_real_signature_and_expected_claims(tmp_path):
    material=m.keys(tmp_path)
    key=jwt.PyJWK.from_dict(material['jwks']['keys'][0]).key
    claims=jwt.decode(material['token'],key,algorithms=['ES256'],audience='authenticated',issuer=m.ISSUER)
    assert claims['sub']==m.OWNER and claims['session_id']==m.SESSION
    assert claims['sub']!=claims['session_id']
    assert (tmp_path/'ca.pem').stat().st_mode&0o777==0o600
    with pytest.raises(jwt.InvalidSignatureError):
        jwt.decode(material['token'],jwt.PyJWK.from_dict(m.keys(tmp_path/'other')['jwks']['keys'][0]).key,
                   algorithms=['ES256'],audience='authenticated',issuer=m.ISSUER)


def test_local_fixture_protocol_no_provider_contact(tmp_path):
    services=m.Services(tmp_path)
    _,port=services.start(directory=tmp_path)
    try:
        base='http://127.0.0.1:'+str(port)
        models=json.load(urllib.request.urlopen(base+'/v1/models'))
        assert models['data']==[{'id':m.MODEL}]
        request=urllib.request.Request(base+'/v1/chat/completions',data=json.dumps({'model':m.MODEL,'messages':[]}).encode(),headers={'Content-Type':'application/json'})
        response=json.load(urllib.request.urlopen(request))
        assert response['usage']['total_tokens']==11 and len(services.calls)==1
        assert json.loads(response['choices'][0]['message']['content'])['complete'] is False
    finally:services.close()


def test_tls_certificate_trusted_only_by_fixture_ca(tmp_path):
    from cryptography import x509
    services=m.Services(tmp_path)
    try:
        cert=x509.load_pem_x509_certificate((tmp_path/'server.pem').read_bytes())
        names=cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
        assert names.get_values_for_type(x509.DNSName)==[m.AUTH_HOST]
        context=ssl.create_default_context(cafile=str(tmp_path/'ca.pem'))
        assert context.verify_mode==ssl.CERT_REQUIRED and context.check_hostname
    finally:services.close()


def test_fixture_hold_cannot_finish_before_explicit_release(tmp_path):
    import threading
    services=m.Services(tmp_path)
    _,port=services.start(directory=tmp_path)
    result=[]
    def call():
        request=urllib.request.Request('http://127.0.0.1:'+str(port)+'/v1/chat/completions',data=json.dumps({'model':m.MODEL,'messages':[{'role':'user','content':'EXACT_IMAGE_STOP'}]}).encode(),headers={'Content-Type':'application/json'})
        result.append(json.load(urllib.request.urlopen(request)))
    worker=threading.Thread(target=call);worker.start()
    try:
        assert services.active.wait(3) and not result
        request=urllib.request.Request('http://127.0.0.1:'+str(port)+'/fixture/release',data=b'',method='POST')
        assert json.load(urllib.request.urlopen(request))=={'released':True}
        worker.join(3)
        assert not worker.is_alive() and len(result)==1 and not services.active.is_set()
    finally:services.release.set();worker.join(3);services.close()
