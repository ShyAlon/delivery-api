"""Runner safety and reporting contracts, without invoking the fixture suite recursively."""
import importlib.util,json,subprocess
from pathlib import Path
import pytest
ROOT=Path(__file__).parents[1]
spec=importlib.util.spec_from_file_location('hurdles',ROOT/'scripts/ops/validate-prior-hurdles.py');h=importlib.util.module_from_spec(spec);spec.loader.exec_module(h)

@pytest.mark.parametrize('url',['https://example.com','http://0.0.0.0:8001','http://user:pass@localhost:8001','http://localhost:8001/path','http://localhost:8001?token=x'])
def test_only_loopback_preview_origin(url):
    with pytest.raises(ValueError):h.local_url(url)


def test_fixture_process_strips_live_credentials_and_uses_fenced_private_output(monkeypatch,tmp_path,capsys):
    monkeypatch.setenv('DEEPSEEK_API_KEY','must-not-pass');monkeypatch.setenv('DELIVERY_DATABASE_URL','must-not-pass');monkeypatch.setenv('DELIVERY_TEST_POSTGRES_DSN','must-not-pass')
    def run(args,**kw):
        env=kw['env'];assert all(k not in env for k in ('DEEPSEEK_API_KEY','DELIVERY_DATABASE_URL','DELIVERY_TEST_POSTGRES_DSN'))
        hook=Path(env['PYTHONPATH'])/'sitecustomize.py';assert 'socket.socket.connect=guard' in hook.read_text()
        assert env['REQUI_TEST_ARTIFACTS']==str(tmp_path/'screenshots')
        rows=[{'nodeid':node+'::example' if '::' not in node else node,'when':'call','outcome':'passed'} for nodes in h.CASES.values() for node in nodes]
        Path(env['HURDLE_REPORT']).write_text(json.dumps({'reports':rows}));return subprocess.CompletedProcess(args,0)
    monkeypatch.setattr(h.subprocess,'run',run)
    assert h.fixture_suite(tmp_path)
    assert capsys.readouterr().out.count('PASS |')==6


def test_skipped_tests_are_not_passes(monkeypatch,tmp_path,capsys):
    def run(args,**kw):
        Path(kw['env']['HURDLE_REPORT']).write_text(json.dumps({'reports':[{'nodeid':'tests/test_account_debug_capability.py::example','when':'setup','outcome':'skipped'}]}));return subprocess.CompletedProcess(args,0)
    monkeypatch.setattr(h.subprocess,'run',run)
    assert not h.fixture_suite(tmp_path)
    assert capsys.readouterr().out.count('NOT CHECKED |')==6


def test_anonymous_preview_entrypoints_report_optional_state_not_checked(pg_browser,tmp_path,capsys):
    _,base,_=pg_browser
    result=subprocess.run([__import__('sys').executable,'-c',"import importlib.util,sys;from pathlib import Path;s=importlib.util.spec_from_file_location('h',sys.argv[1]);m=importlib.util.module_from_spec(s);s.loader.exec_module(m);raise SystemExit(0 if m.preview_checks(sys.argv[2],None,Path(sys.argv[3])) else 1)",str(ROOT/'scripts/ops/validate-prior-hurdles.py'),base,str(tmp_path)],capture_output=True,text=True,timeout=45)
    assert result.returncode==0,result.stdout+result.stderr
    output=result.stdout
    assert 'PASS | running preview health' in output
    assert 'PASS | running preview anonymous browser boot' in output
    assert 'NOT CHECKED | signed-in running preview' in output
