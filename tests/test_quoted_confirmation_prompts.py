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

def test_launch_shell_prints_literal_quotes():
    import subprocess
    line=next(x for x in (ROOT/'scripts/ops/preview-exec.sh').read_text().splitlines() if x.startswith("printf 'Type"))
    result=subprocess.run(['bash','-c',line],capture_output=True,text=True,check=True)
    assert result.stdout.startswith('Type "yes" ')
    assert '[ "$A" = yes ]' in (ROOT/'scripts/ops/preview-exec.sh').read_text()
