"""Host telemetry and model working-set estimates are different facts."""
import json,sys
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts/docker'))

G=1024**3

def test_mac_snapshot_reads_host_pages_without_counting_purgeable_twice():
    from host_memory import parse_mac
    value=parse_mac(str(16*G),'''Mach Virtual Memory Statistics: (page size of 16384 bytes)
Pages free: 65536.
Pages inactive: 131072.
Pages speculative: 65536.
Pages purgeable: 65536.
Pages occupied by compressor: 100.
''',now=100)
    assert value['total_bytes']==16*G
    assert value['available_estimate_bytes']==4*G
    assert value['source']=='macos-launcher' and value['sampled_at']==100
    assert value['available_method']=='free+inactive+speculative pages; reclaimability estimate'

@pytest.mark.parametrize('text',['','Mach Virtual Memory Statistics: (page size of 0 bytes)\nPages free: 1.','Mach Virtual Memory Statistics: (page size of 16384 bytes)\nPages free: -1.'])
def test_partial_host_data_is_not_a_fit(text):
    from host_memory import parse_mac
    with pytest.raises(ValueError):parse_mac(str(16*G),text,now=100)

def test_snapshot_is_fresh_and_bound_to_host_route_not_container_or_remote(tmp_path):
    from delivery_api.model_memory import host_snapshot
    p=tmp_path/'memory.json';p.write_text(json.dumps({'schema':1,'source':'macos-launcher','sampled_at':100,'total_bytes':16*G,'available_estimate_bytes':8*G,'available_method':'fixture','platform':'Darwin','machine':'arm64'}))
    assert host_snapshot(p,'http://host.docker.internal:11434/v1',now=110)['total_bytes']==16*G
    assert host_snapshot(p,'http://host.docker.internal:11434/v1',now=145) is None
    for base in ('http://localhost:11434/v1','http://other-host:11434/v1','http://host.docker.internal:7777/v1'):
        assert host_snapshot(p,base,now=110) is None
    p.write_text('{broken')
    assert host_snapshot(p,'http://host.docker.internal:11434/v1',now=110) is None

def test_architecture_specific_kv_does_not_treat_qwen35_as_full_attention():
    from delivery_api.model_memory import estimate
    a=estimate('qwen3.5:9b',131072,6*G)
    b=estimate('qwen3:8b',32768,5*G)
    assert a['kv_estimate_bytes']==4*G
    assert b['kv_estimate_bytes']==int(4.5*G)
    assert a['estimated'] and a['assumed_parallel_sequences']==1
    assert a['working_estimate_bytes']>10*G
    assert estimate('unknown',32768,5*G) is None

def test_unfit_blocks_before_load_and_loaded_allocation_is_not_counted_twice():
    from delivery_api.model_memory import readiness
    snapshot={'total_bytes':16*G,'available_estimate_bytes':8*G}
    a=readiness('qwen3.5:9b',131072,6*G,snapshot,[])
    assert a['state']=='estimated_unfit' and a['blocked']
    assert 'estimated' in a['message'].lower() and 'GiB' in a['message']
    b=readiness('qwen3.5:9b',131072,6*G,snapshot,[{'id':'qwen3.5:9b','resident_bytes':7*G,'context_length':131072}])
    assert not b['blocked'] and b['state']=='estimate_with_headroom'
    assert b['incremental_estimate_bytes']==max(0,b['working_estimate_bytes']-7*G)
    assert not readiness('qwen3.5:9b',131072,6*G,None,[])['blocked']
    assert readiness('qwen3.5:9b',131072,6*G,None,[])['state']=='memory_not_checked'


def test_sampler_lifetime_atomic_readonly_mount_and_failure_invalidates(tmp_path):
    from host_memory import HostMemory
    values=[{'schema':1,'source':'macos-launcher'},None]
    with HostMemory(tmp_path/'host',reader=lambda:values.pop(0),interval=100) as sampler:
        assert sampler.mount_arguments()==['--mount','type=bind,source='+str(tmp_path/'host')+',target=/run/requi-host,readonly']
        assert json.loads(sampler.snapshot.read_text())['source']=='macos-launcher'
        assert sampler.snapshot.stat().st_mode&0o777==0o644
        sampler.update()
        assert json.loads(sampler.snapshot.read_text())['source']=='unavailable'
    assert not sampler.snapshot.exists() and not sampler.thread.is_alive()


def test_registry_preserves_unfit_choices_for_plain_refusal(monkeypatch,tmp_path):
    from delivery_api import model_registry as registry,model_memory
    monkeypatch.setattr(model_memory,'host_snapshot',lambda **k:{'total_bytes':8*G,'available_estimate_bytes':2*G})
    monkeypatch.setattr(registry,'probe',lambda *a:{'reachable':True,'installed_models':[{'id':'qwen3.5:9b','digest':'56671c2ab938'+'a'*52,'file_bytes':6*G,'capabilities':['completion','thinking'],'thinking':{'values':[False]},'native_context':262144}]})
    records=registry.discover([],{})
    assert len(records)==1 and records[0]['memory']['blocked']
    with pytest.raises(ValueError,match='Estimated additional model memory'):registry.resolve(records,'ollama','qwen3.5:9b')
    assert records[0]['payload']['context']['context_tokens']==131072


def test_postload_verifies_context_and_records_allocation_without_second_prompt():
    import httpx
    from delivery_api.ollama_native import observe_loaded
    from delivery_api.provider_http import ProviderHTTPError
    context={'model':'qwen3.5:9b','context_tokens':131072}
    rows=[{'name':'qwen3.5:9b','context_length':131072,'size':10*G,'size_vram':10*G}];calls=[]
    def respond(request):
        calls.append(request)
        assert request.method=='GET' and request.url.path=='/api/ps'
        return httpx.Response(200,json={'models':rows})
    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        assert observe_loaded(client,'http://ollama.invalid/v1/chat/completions',context)=={'context_tokens':131072,'resident_bytes':10*G,'vram_bytes':10*G}
        rows[0]['context_length']=4096
        with pytest.raises(ProviderHTTPError,match='different context'):observe_loaded(client,'http://ollama.invalid/v1/chat/completions',context)
        rows.clear()
        with pytest.raises(ProviderHTTPError,match='could not be verified'):observe_loaded(client,'http://ollama.invalid/v1/chat/completions',context)
    assert len(calls)==3


def test_low_memory_start_refresh_refuses_before_pin_or_child(pg_browser,monkeypatch):
    from playwright.sync_api import expect
    from types import SimpleNamespace
    from delivery_api import app as module,cloud_runtime,model_pins,model_registry,model_memory
    from delivery_api.tenancy import tenant_context
    page,base,workspace=pg_browser
    host={'total_bytes':16*G,'available_estimate_bytes':15*G}
    monkeypatch.setattr(model_memory,'host_snapshot',lambda **k:dict(host))
    listing={'reachable':True,'installed_models':[{'id':'qwen3.5:9b','file_bytes':6*G,'digest':'56671c2ab938'+'a'*52,'capabilities':['completion','thinking'],'thinking':{'values':[False]},'native_context':262144}]}
    monkeypatch.setattr(model_registry,'probe',lambda *a:listing)
    monkeypatch.setattr(model_pins.PinRepository,'list',lambda self:[])
    factory=model_pins.DurableGateways(workspace.store,{})
    monkeypatch.setattr(cloud_runtime,'gateway_factory',lambda:factory)
    monkeypatch.setattr(cloud_runtime,'installed',lambda:True)
    monkeypatch.setattr(module,'cloud_execution_admitted',lambda:True)
    previews=[]
    monkeypatch.setattr(module,'engine_client',SimpleNamespace(default_route=lambda:{'route':'ollama','model':'qwen3.5:9b'},preview_route=lambda *a:previews.append(a)))
    with tenant_context(workspace.scope):task=workspace.store.create_task('Check memory before Start')
    page.goto(base+'/tasks/'+task['task_id']+'/view')
    page.locator('[data-start-route=ollama]').click()
    expect(page.locator('#startmemory')).to_contain_text('Estimated model memory')
    expect(page.locator('#startengine')).to_be_enabled()
    page.screenshot(path='/downloads/stage2-memory-desktop.png',full_page=True)
    page.set_viewport_size({'width':390,'height':844});page.wait_for_timeout(150)
    page.screenshot(path='/downloads/stage2-memory-mobile.png',full_page=True)
    assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
    host['available_estimate_bytes']=2*G
    page.locator('#startengine').click()
    expect(page.locator('#startstatus')).to_contain_text('exceeds current estimated available host memory')
    assert previews==[]
    with tenant_context(workspace.scope):
        assert workspace.store.task(task['task_id'])['state']=='not_started'
        assert workspace.store.task_model_pin(task['task_id'])['environment'] is None
    page.reload()
    expect(page.locator('#startengine')).to_be_disabled()
    expect(page.locator('.route-unavailable')).to_contain_text('Estimated additional model memory')
    factory.close()


@pytest.mark.parametrize('launcher',['local_image_preview','local_pg_preview'])
def test_attended_launchers_own_sampler_lifetime_without_new_service(tmp_path,monkeypatch,launcher):
    import importlib
    from types import SimpleNamespace
    import host_memory
    module=importlib.import_module(launcher);seen=[]
    monkeypatch.setattr(host_memory,'sample',lambda:{'schema':1,'source':'fixture'})
    def launch(*args,host_memory_factory=None,**kwargs):
        host_memory=host_memory_factory()
        assert host_memory is not None and host_memory.snapshot.exists()
        seen.append(host_memory)
        return 'fixture-running'
    monkeypatch.setattr(module,'_preview',launch)
    if launcher=='local_image_preview':result=module.preview(SimpleNamespace(path=tmp_path/'state.json'),env_file=tmp_path/'service.env',apply=True)
    else:result=module.preview(root=tmp_path,state=None,source=tmp_path/'service.env',home=tmp_path,apply=True)
    assert result=='fixture-running'
    assert len(seen)==1 and not seen[0].thread.is_alive() and not seen[0].snapshot.exists()


def test_runtime_telemetry_mount_is_readonly_and_does_not_pass_a_capacity_setting(tmp_path,monkeypatch):
    import local_pg_preview as launcher
    from host_memory import HostMemory
    from types import SimpleNamespace
    resource=launcher.names('a'*32);commands=[]
    monkeypatch.setattr('image_settings.validate',lambda p:{'DELIVERY_DATABASE_URL':launcher.DSN})
    monkeypatch.setattr(launcher,'controller',lambda *a:None)
    def inspect(kind,name,nonce):
        if kind=='network':return {'Name':name}
        if name==resource['app'] and commands:return {'Image':'sha256:'+'e'*64,'State':{'Health':{'Status':'healthy'}}}
        return None
    monkeypatch.setattr(launcher,'inspect_owned',inspect)
    monkeypatch.setattr(launcher,'command',lambda args:commands.append(args))
    with HostMemory(tmp_path/'host',reader=lambda:None,interval=100) as sampler:
        launcher.start_app(resource,'a'*32,'sha256:'+'e'*64,tmp_path/'local.env',8002,host_memory=sampler)
        run=commands[0]
        assert any('target=/run/requi-host,readonly' in a for a in run)
        assert '--memory' in run and run[run.index('--memory')+1]=='3g'
        assert not any('RAM=' in a or 'MEMORY_BYTES=' in a for a in run)


def test_parent_transport_observes_one_load_not_one_per_call():
    import httpx
    from delivery_api.provider_http import ParentProviderHTTP
    from delivery_api.model_registry import local_capability
    context=local_capability('qwen3.5:9b','56671c2ab938'+'a'*52)['context'];calls=[]
    def respond(request):
        calls.append(request.method)
        if request.method=='GET':return httpx.Response(200,json={'models':[{'name':'qwen3.5:9b','context_length':131072,'size':10*G,'size_vram':10*G}]})
        return httpx.Response(200,json={'model':'qwen3.5:9b','done':True,'done_reason':'stop','message':{'role':'assistant','content':'ok'},'prompt_eval_count':1,'eval_count':1})
    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        transport=ParentProviderHTTP(client,'http://local.invalid/v1/chat/completions',{},local_context=context)
        raw=json.dumps({'model':'qwen3.5:9b','max_tokens':10,'messages':[]}).encode()
        transport.send(raw);transport.send(raw)
        assert calls==['POST','GET','POST']
        assert transport.local_observation['resident_bytes']==10*G


@pytest.mark.parametrize('value',[[],None,{}, {'schema':1,'source':'macos-launcher','platform':'Darwin','machine':'arm64','sampled_at':float('nan')}, {'schema':1,'source':'macos-launcher','platform':'Darwin','machine':'arm64','sampled_at':200,'total_bytes':16*G,'available_estimate_bytes':8*G}])
def test_malformed_and_future_host_readings_fail_to_unknown(tmp_path,value):
    from delivery_api.model_memory import host_snapshot
    p=tmp_path/'memory.json';p.write_text(json.dumps(value))
    assert host_snapshot(p,'http://host.docker.internal:11434/v1',now=110) is None
