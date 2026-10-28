import ast
from pathlib import Path
import pytest

SCRIPT=Path(__file__).parents[1]/'scripts/diagnostics/check-diagnostics-target.sh'


def identifiers():
    source=SCRIPT.read_text().split("<<'PY'\n",1)[1].rsplit('\nPY',1)[0]
    tree=ast.parse(source)
    function=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='identifiers')
    ns={}
    exec('import re\nfrom urllib.parse import urlsplit,unquote\nfrom psycopg.conninfo import conninfo_to_dict',ns)
    exec(compile(ast.Module(body=[function],type_ignores=[]),'identifiers','exec'),ns)
    return ns['identifiers']


@pytest.mark.parametrize('value',[
    'postgresql://postgres.fakeproject:fake%40password@aws-0-us-east-1.pooler.supabase.com:5432/postgres',
    'postgres://postgres.fakeproject:fake%2Fpassword@aws-0-us-east-1.pooler.supabase.com:5432/postgres',
    "host=aws-0-us-east-1.pooler.supabase.com user=postgres.fakeproject password='fake @ / password' dbname=postgres",
])
def test_identifiers_use_libpq_semantics_without_secret_output(value):
    assert identifiers()(value)==('aws-0-us-east-1.pooler.supabase.com','fakeproject')


def test_effective_connected_identity_overrides_misparsed_input():
    assert identifiers()('postgresql://postgres.fakeproject',{'host':'aws-0-us-east-1.pooler.supabase.com','user':'postgres.fakeproject'})==('aws-0-us-east-1.pooler.supabase.com','fakeproject')


def test_shared_host_without_username_remains_unknown():
    assert identifiers()('postgresql://aws-0-us-east-1.pooler.supabase.com/postgres')[1]=='unknown'


@pytest.mark.parametrize('value,expected',[
    ('https://fakeproject.supabase.co',('fakeproject.supabase.co','fakeproject')),
    ('postgresql://postgres:fake@db.fakeproject.supabase.co/postgres',('db.fakeproject.supabase.co','fakeproject')),
    ('not a connection string',('(unparseable connection identifier)','unknown')),
])
def test_api_direct_and_invalid_identifiers(value,expected):
    assert identifiers()(value)==expected
