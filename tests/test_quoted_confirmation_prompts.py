"""Displayed quoted tokens must match the existing exact comparison values."""
import importlib.util
from pathlib import Path
import pytest
ROOT=Path(__file__).parents[1]

def owner():
    spec=importlib.util.spec_from_file_location('quoted_owner',ROOT/'scripts/owner/apply-pg-cutover.py')
    m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m

def test_migration_phrase_quotes_exact_compared_value(monkeypatch):
    m=owner();row={'file':'sample.sql','status':'pending','destructive':[]};prompts=[]
    def decline(text):prompts.append(text);return ''
    with pytest.raises(ValueError,match='confirmation did not match'):
        m.apply(None,[row],None,ROOT,'delivery_app',decline)
    assert 'Type "APPLY 1 MIGRATIONS TO delivery_app" to apply' in prompts[0]

def test_function_review_quotes_exact_file_and_hash():
    m=owner();name,digest=next(iter(m.PROJECTION_FUNCTION_REVIEW.items()));prompts=[]
    def accept(text):prompts.append(text);return 'REVIEW FUNCTIONS '+name+' SHA256 '+digest
    assert m.review_projection_functions([{'file':name,'sha256':digest,'status':'pending','destructive':['DELETE']}],accept)=={name:digest}
    assert prompts==['Type "REVIEW FUNCTIONS '+name+' SHA256 '+digest+'": ']

def test_launch_shell_prints_literal_quotes(monkeypatch,capsys):
    import sys
    sys.path.insert(0,str(ROOT/'scripts/docker'))
    from image_workflow import confirm
    monkeypatch.setattr('builtins.input',lambda:'yes')
    assert confirm('run the local image stage')
    assert 'Waiting for input: Type "yes" ' in capsys.readouterr().err
