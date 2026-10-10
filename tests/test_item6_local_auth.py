"""Local test-auth fixture verifies exact signed identity and active session."""
import os
import pytest
from local_auth import DisposableSignup
from private_adapters import IdentityAdapter
from delivery_api.postgres_store import PostgresStore
from test_item6_isolation import config
ADMIN=os.getenv('ITEM6_TEST_ADMIN_DSN')
pytestmark=pytest.mark.skipif(not ADMIN,reason='explicit isolated PG required')

def test_disposable_signup_verifies_signature_and_does_not_replay(tmp_path):
 import psycopg
 from psycopg.conninfo import conninfo_to_dict,make_conninfo
 info=conninfo_to_dict(ADMIN);assert info['dbname'].startswith('requi_item6_')
 signup=DisposableSignup(admin_connection=lambda:psycopg.connect(ADMIN),mode='hermetic')
 store=PostgresStore(make_conninfo(**(info|{'user':'delivery_app'})))
 adapter=IdentityAdapter(signup=signup,verifier=signup.verifier,store=store)
 receipt=adapter(config(tmp_path),{})
 assert receipt['signature_verified'] and receipt['fresh_signup'] and receipt['active_session']
 with pytest.raises(ValueError,match='replay'):signup()
