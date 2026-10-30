import importlib.util
from pathlib import Path
import pytest
spec=importlib.util.spec_from_file_location('compare',Path(__file__).resolve().parents[1]/'scripts/diagnostics/compare-answer-progress.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)

def test_extracts_and_fails_loud(tmp_path):
    p=tmp_path/'log';p.write_text("http.request | path='/api/tasks/t/answer' status='200' duration_ms='142588' db_queries='576' db_ms='124099'\nprogress.ingest.timing | events='5' ingest_ms='10349' fetch_ms='2750'\nloop.lag | lag_ms='10810'")
    result=m.scan(p);assert result['answers'][0]['db_queries']==576
    assert result['progress_pages'][0]['ingest_ms']==10349
    assert result['max_logged_loop_lag_ms']==10810
    p.write_text('nothing');
    with pytest.raises(ValueError):m.scan(p)
    p.write_text("http.request path='/answer' status='500'")
    with pytest.raises(ValueError):m.scan(p)
