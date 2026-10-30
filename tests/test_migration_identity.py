import importlib.util
from pathlib import Path
import pytest
ROOT=Path(__file__).parents[1]
SPEC=importlib.util.spec_from_file_location('migration_identity',ROOT/'scripts/owner/apply-pg-cutover.py')
m=importlib.util.module_from_spec(SPEC)

def test_script_does_not_claim_app_identity_after_set_role():
    SPEC.loader.exec_module(m)
    assert callable(m.identity)
    class R:
        def fetchone(self):return ('postgres','delivery_app')
    class C:
        def execute(self,sql):assert 'session_user' in sql and 'current_user' in sql;return R()
    assert m.identity(C())==('postgres','delivery_app')

def test_insufficient_admin_rights_fail_plainly_before_migration():
    SPEC.loader.exec_module(m)
    class R:
        def fetchone(self):return (False,False)
    class C:
        def execute(self,*args):return R()
    with pytest.raises(ValueError,match='lacks CREATE ROLE or CREATE on public'):m.check_admin(C())
