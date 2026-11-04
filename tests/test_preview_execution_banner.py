"""Production status never treats container health/log text as route admission."""
import importlib.util
from pathlib import Path
import pytest
spec=importlib.util.spec_from_file_location('preview_status',Path(__file__).resolve().parents[1]/'scripts/docker/preview_status.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)


@pytest.mark.parametrize('health,routes,service,execution',[
    ({'ok':True},None,'HEALTHY','UNVERIFIED'),
    ({'ok':False},None,'UNVERIFIED','UNVERIFIED'),
    ({'ok':True},{},'HEALTHY','UNVERIFIED'),
    ({'ok':True},{'routes':[]},'HEALTHY','UNVERIFIED'),
    ({'ok':True},{'routes':[{'available':False}]},'HEALTHY','UNAVAILABLE'),
    ({'ok':True},{'routes':[{'available':True}]},'HEALTHY','AVAILABLE'),
])
def test_banner_uses_current_typed_facts_not_last_log_line(health,routes,service,execution):
    value=m.summarize(health,routes)
    assert value['service']==service and value['execution']==execution
    assert 'Both routes admitted' not in value['message']
    if execution=='AVAILABLE':assert 'not permission' in value['message']
