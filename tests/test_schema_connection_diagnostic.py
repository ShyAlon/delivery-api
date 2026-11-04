"""Schema connection failures carry safe endpoint facts, never credential text."""
import importlib.util
from pathlib import Path
import pytest
ROOT=Path(__file__).parents[1]

def module():
    spec=importlib.util.spec_from_file_location('schema_connection',ROOT/'scripts/ops/schema-readiness.py')
    m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m

@pytest.mark.parametrize('url,kind',[
    ('postgresql://user:secret@aws-0-eu.pooler.supabase.com:5432/postgres','Supabase session pooler'),
    ('postgresql://user:secret@aws-0-eu.pooler.supabase.com:6543/postgres','Supabase transaction pooler'),
    ('postgresql://user:secret@db.project.supabase.co:5432/postgres','Supabase direct'),
    ('postgresql://user:secret@private.example:5432/postgres','other TCP endpoint'),
])
def test_timeout_uses_safe_endpoint_class_and_actual_timeout(monkeypatch,capsys,url,kind):
    import psycopg
    m=module();calls=[]
    def fail(*a,**kw):calls.append(kw);raise psycopg.errors.ConnectionTimeout('private-error '+url)
    monkeypatch.setattr(psycopg,'connect',fail)
    assert m.check_connection(url)==2
    output=capsys.readouterr().out
    assert 'SCHEMA CHECK FAILED: ConnectionTimeout' in output
    assert kind in output and 'connect_timeout=10s' in output and 'no application retry' in output
    assert 'Check' in output and 'connection' in output and 'VPN' in output
    assert 'secret' not in output and 'user' not in output and 'private-error' not in output
    assert calls==[{'connect_timeout':10,'autocommit':True}]


def test_timeout_does_not_offer_migration_apply(monkeypatch):
    m=module()
    assert m.review_and_recheck(lambda:2,lambda:pytest.fail('must not offer apply'))==2


def test_endpoint_metadata_never_echoes_invalid_or_multi_host_values():
    m=module()
    for value in ('not a connection string secret', 'host=private1,private2 password=secret port=5432,5433', 'host=private password=secret port=secret'):
        text=m.endpoint_class(value)
        assert 'private' not in text and 'secret' not in text


def test_endpoint_metadata_respects_libpq_environment(monkeypatch):
    m=module();monkeypatch.setenv('PGHOST','aws-0-eu.pooler.supabase.com');monkeypatch.setenv('PGPORT','6543')
    assert m.endpoint_class('dbname=postgres')=='Supabase transaction pooler; port=6543'
