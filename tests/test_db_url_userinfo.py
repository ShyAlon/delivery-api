import importlib.util
from pathlib import Path
import pytest
ROOT=Path(__file__).parents[1]
SPEC=importlib.util.spec_from_file_location('safe_db_url',ROOT/'scripts/owner/apply-pg-cutover.py');m=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(m)

@pytest.mark.parametrize('password',['before@AFTERSECRET','before/AFTERSECRET','before?AFTERSECRET','before#AFTERSECRET','bad%SECRET'])
def test_raw_password_delimiters_never_reach_diagnostics(password):
    for label in ('admin','app'):
        with pytest.raises(ValueError) as error:m.parse_database_url('postgresql://postgres.project:'+password+'@db.example:5432/postgres',label)
        assert 'SECRET' not in str(error.value) and 'before' not in str(error.value)
        assert 'URL-encoded' in str(error.value)

def test_encoded_password_is_preserved_and_parsed_correctly():
    result=m.parse_database_url('postgresql://postgres.project:before%40AFTER%2F%3F%23@db.example:5432/postgres','admin')
    assert result['host']=='db.example' and result['password']=='before@AFTER/?#'
