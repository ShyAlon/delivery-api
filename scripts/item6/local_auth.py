"""Private disposable local test-auth, not a Supabase account or production auth.

No server is mounted by delivery-api. Caller owns a fresh loopback PG database.
Signing key stays in this process. New signup always creates a new identity and
session; existing tokens/accounts are never reused to manufacture a clean run.
"""
import time,uuid
from types import SimpleNamespace
import jwt
from cryptography.hazmat.primitives.asymmetric import ec
from delivery_api.supabase_auth import SupabaseIdentityVerifier


class DisposableSignup:
    issuer='https://item6-local-test.supabase.co'
    def __init__(self,*,admin_connection,mode):
        if mode not in ('hermetic','mac_acceptance'):raise ValueError('explicit test mode required')
        self.connect=admin_connection;self.mode=mode;self.key=ec.generate_private_key(ec.SECP256R1());self.used=False
        self.verifier=SupabaseIdentityVerifier(self.issuer,key_provider=SimpleNamespace(get_signing_key_from_jwt=self.signing_key))
    def signing_key(self,token):return SimpleNamespace(key=self.key.public_key())
    def __call__(self):
        if self.used:raise ValueError('no signup replay or existing-account fallback')
        self.used=True;subject=str(uuid.uuid4());session=str(uuid.uuid4());now=int(time.time())
        with self.connect() as c:
            db=c.execute('SELECT current_database()').fetchone()[0]
            if not db.startswith('requi_item6_'):raise ValueError('disposable test database required')
            c.execute('INSERT INTO auth.users(id) VALUES(%s)',(subject,))
            c.execute('INSERT INTO auth.sessions(id,user_id) VALUES(%s,%s)',(session,subject))
        token=jwt.encode({'sub':subject,'session_id':session,'role':'authenticated','iat':now,'exp':now+86400,
            'iss':self.issuer+'/auth/v1','aud':'authenticated'},self.key,algorithm='ES256',headers={'kid':'item6-ephemeral'})
        return {'token':token,'fresh_signup':True,'source_mode':self.mode}
