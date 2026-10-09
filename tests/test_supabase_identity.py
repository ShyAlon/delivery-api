import time
from types import SimpleNamespace
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from delivery_api.supabase_auth import SupabaseIdentityVerifier
from delivery_api.tenancy import TenantAccessDenied

KEY = ec.generate_private_key(ec.SECP256R1())
OTHER = ec.generate_private_key(ec.SECP256R1())
URL = 'https://test-project.supabase.co'
SUB = '00000000-0000-0000-0000-000000000001'

class Keys:
    def get_signing_key_from_jwt(self, token): return SimpleNamespace(key=KEY.public_key())

def token(**changes):
    body = dict(sub=SUB, exp=int(time.time())+300, iat=int(time.time()),
                iss=URL+'/auth/v1', aud='authenticated',role='authenticated',session_id=SUB)
    body.update(changes)
    return jwt.encode(body,KEY,algorithm='ES256',headers={'kid':'test'})


def test_verified_subject_only_no_metadata_admin():
    identity = SupabaseIdentityVerifier(URL,key_provider=Keys()).verify(
        token(email='other@invalid',user_metadata={'owner_id':'evil','admin':True}))
    assert identity.owner_id == SUB


@pytest.mark.parametrize('changes', [dict(exp=1),dict(iat=int(time.time())+900),
 dict(iss='https://evil.invalid/auth/v1'),dict(aud='service_role'),
 dict(role='service_role'),dict(sub='local-admin'),dict(exp=None)])
def test_bad_claims_denied(changes):
    with pytest.raises(TenantAccessDenied):
        SupabaseIdentityVerifier(URL,key_provider=Keys()).verify(token(**changes))


def test_wrong_signature_denied():
    body=jwt.decode(token(),options={'verify_signature':False})
    forged=jwt.encode(body,OTHER,algorithm='ES256')
    with pytest.raises(TenantAccessDenied): SupabaseIdentityVerifier(URL,key_provider=Keys()).verify(forged)


def test_symmetric_and_unsigned_tokens_denied():
    body=jwt.decode(token(),options={'verify_signature':False})
    for algorithm,key in [('HS256','x'*32),('none',None)]:
        with pytest.raises(TenantAccessDenied):
            SupabaseIdentityVerifier(URL,key_provider=Keys()).verify(jwt.encode(body,key,algorithm=algorithm))


@pytest.mark.parametrize('url', ['http://x.supabase.co','https://supabase.co.evil.invalid',
 'https://x.supabase.co/override','https://u:p@x.supabase.co','https://x.supabase.co?url=evil'])
def test_configuration_cannot_redirect_auth_to_arbitrary_origin(url):
    with pytest.raises(ValueError): SupabaseIdentityVerifier(url)
