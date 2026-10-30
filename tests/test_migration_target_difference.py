import importlib.util
from pathlib import Path
import pytest
ROOT=Path(__file__).parents[1]
SPEC=importlib.util.spec_from_file_location('target_difference',ROOT/'scripts/owner/apply-pg-cutover.py');m=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(m)

def test_difference_names_only_mismatching_nonsecret_components():
    with pytest.raises(ValueError) as error:m.check_targets({'host':'db.project.supabase.co','port':'6543','dbname':'postgres','user':'postgres.project','password':'SUPERSECRET'}, {'host':'pooler.supabase.com','port':'5432','dbname':'postgres','user':'delivery_app.project','password':'APPSECRET'})
    text=str(error.value)
    assert 'host: admin=db.project.supabase.co; app=pooler.supabase.com' in text
    assert 'port: admin=6543; app=5432' in text
    assert 'database:' not in text and 'user' not in text and 'SECRET' not in text
    assert 'session-pooler' in text

def test_different_login_names_are_expected_not_a_target_difference():
    m.check_targets({'host':'same','dbname':'postgres','user':'postgres.project'}, {'host':'same','port':'5432','dbname':'postgres','user':'delivery_app.project'})
