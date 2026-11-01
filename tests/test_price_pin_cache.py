"""No public network: launch price fetch, fallback, exact freshness and identity boundaries."""
import datetime,json,runpy,sys
import pytest
from delivery_api import price_pin_cache as cache,production_gateway as pg,vendor_sources as vs
from test_vendor_terms import snapshot


def pin(hours):
    snap=snapshot(hours);snap['model']='deepseek-flash'
    return snap,'\n'.join([pg.PRICES_ENV+'='+json.dumps([{'provider':'deepseek','model':'deepseek-flash','provider_snapshot':snap}]),pg.CEILING_ENV+'='+json.dumps({'model':'deepseek-flash','context_tokens':128000,'maximum_output_tokens':16384})])+'\n'


@pytest.mark.parametrize('hours,allowed',[(0,True),(23.99,True),(24,False),(25,False),(-1,False)])
def test_age_boundary_and_unchanged_timestamp(tmp_path,hours,allowed):
    snap,text=pin(hours);path=tmp_path/'cache.json';cache.save_cached_pin(path,text,snap['fetched_at'])
    before=path.read_bytes()
    if allowed:
        assert cache.load_cached_pin(path,'deepseek-flash',128000,16384)[0]==text
    else:
        with pytest.raises(Exception):cache.load_cached_pin(path,'deepseek-flash',128000,16384)
    assert path.read_bytes()==before


def test_missing_corrupt_model_and_bounds_refuse(tmp_path):
    path=tmp_path/'cache.json'
    with pytest.raises(FileNotFoundError):cache.load_cached_pin(path,'m',1,1)
    path.write_text('bad')
    with pytest.raises(ValueError):cache.load_cached_pin(path,'m',1,1)
    snap,text=pin(1);cache.save_cached_pin(path,text,snap['fetched_at'])
    with pytest.raises(ValueError,match='model mismatch'):cache.load_cached_pin(path,'other',128000,16384)
    with pytest.raises(ValueError,match='bounds mismatch'):cache.load_cached_pin(path,'deepseek-flash',128000,1)


@pytest.mark.parametrize('hours,exit_code',[(1,0),(24,2)])
def test_real_pin_entrypoint_fetch_failure_uses_cache_only_under_24h(tmp_path,monkeypatch,capsys,hours,exit_code):
    path=tmp_path/'cache.json';out=tmp_path/'out';out.mkdir()
    snap,text=pin(hours);cache.save_cached_pin(path,text,snap['fetched_at'])
    monkeypatch.setenv('REQUI_PRICE_PIN_CACHE',str(path));monkeypatch.setattr(sys,'argv',['pin.py',str(out)])
    monkeypatch.setattr(vs,'fetch_verified_source',lambda u:(_ for _ in ()).throw(ConnectionError('offline')))
    with pytest.raises(SystemExit) as exc:runpy.run_path('scripts/ops/pin.py',run_name='__main__')
    assert exc.value.code==exit_code
    printed=capsys.readouterr().out
    if exit_code==0:
        assert 'WARNING' in printed and (out/'cloud-models.env').read_text()==text
    else:assert 'PIN-FAIL' in printed and not (out/'cloud-models.env').exists()
