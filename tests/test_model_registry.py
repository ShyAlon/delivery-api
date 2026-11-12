"""One exact model identity from readiness to immutable job pin. No live services."""
import copy,json
import httpx,pytest
from delivery_api import model_registry as registry


def local_listing():
    return {'reachable':True,'installed_models':[{'id':'qwen3:8b','digest':'sha256:'+('500a1f067a9f'+'a'*52),'capabilities':['completion','thinking'],'thinking':{'values':[True,False]},'native_context':32768}]}


def test_verified_local_capability_has_no_paid_approval_or_inherited_model(monkeypatch):
    monkeypatch.setattr(registry,'probe',lambda *a:local_listing())
    records=registry.discover([],{'REQUI_PIN_PROVIDERS':'deepseek','DELIVERY_OLLAMA_MODEL':'phi4','DEEPSEEK_API_KEY':'never-copy'})
    assert len(records)==1
    value=records[0];assert value['id']=='ollama:qwen3:8b'
    assert value['payload']['context']['context_tokens']==32768
    assert value['payload']['context']['maximum_output_tokens']==12288
    assert value['payload']['row']['expense_output']==0
    assert 'never-copy' not in str(value) and 'phi4' not in str(value)
    assert registry.resolve(records,'ollama','qwen3:8b')==value
    with pytest.raises(ValueError,match='not ready'):registry.resolve(records,'ollama','phi4')


def test_unreachable_unknown_or_unversioned_local_model_is_not_ready(monkeypatch):
    for listing in ({'reachable':False,'installed_models':[]},{'reachable':True,'installed_models':[{'id':'unknown:latest','digest':'x'}]}, {'reachable':True,'installed_models':[{'id':'qwen3:8b'}]}):
        monkeypatch.setattr(registry,'probe',lambda *a:listing)
        assert registry.discover([],{})==[]


def test_paid_models_still_come_only_from_reviewed_heads(monkeypatch):
    from test_production_gateway import row,CEILING
    monkeypatch.setattr(registry,'probe',lambda *a:{'reachable':False})
    value={'provider':'deepseek','model':row()['model'],'is_default':True,'payload':{'row':row(),'context':dict(CEILING)}}
    assert registry.discover([value],{})==[] # no parent key, no paid readiness
    records=registry.discover([value],{'DEEPSEEK_API_KEY':'test-only'})
    assert records[0]['id']=='deepseek:'+row()['model']


def test_all_phase_budgets_are_checked_before_start(monkeypatch):
    from spec_generator import model_config
    from interview_engine import model_config as interview
    budgets=registry.phase_budgets()
    assert budgets['generator/tests']==model_config._max_tokens_limit(model_config._STAGE_DEFAULTS['tests'].max_tokens)==12288
    assert any(k.startswith('interview/') for k in budgets)
    assert registry.unsupported({'context_tokens':32768,'maximum_output_tokens':12288},budgets)==[]
    failures=registry.unsupported({'context_tokens':32768,'maximum_output_tokens':8192},budgets)
    assert failures==['generator/tests needs 12288 output tokens; this model allows 8192.']
    monkeypatch.setenv('VMODEL_LLM_MAX_TOKENS','20000')
    assert registry.unsupported({'context_tokens':32768,'maximum_output_tokens':12288},registry.phase_budgets())


def test_explicit_endpoint_wins_and_owned_socket_topology_has_a_host_default():
    assert registry.ollama_base({'OLLAMA_BASE_URL':'http://other:123/v1'})=='http://other:123/v1'
    assert registry.ollama_base({'DELIVERY_DATABASE_URL':'postgresql:///requi_local?host=/owned/socket&user=delivery_app'})=='http://host.docker.internal:11434/v1'
    assert registry.ollama_base({})=='http://localhost:11434/v1'


def test_local_selection_persists_run_pin_without_operator_row(store,monkeypatch):
    from delivery_api import model_pins
    from delivery_api.tenancy import tenant_context
    from test_postgres_store import A
    monkeypatch.setattr(model_pins.PinRepository,'list',lambda self:[])
    monkeypatch.setattr(registry,'probe',lambda *a:local_listing())
    factory=model_pins.DurableGateways(store,{'REQUI_PIN_PROVIDERS':'deepseek'})
    with tenant_context(A):
        task=store.create_task('Canonical local model')
        try:
            first=factory.for_task(task['task_id'],('ollama','qwen3:8b'))
            assert first.models=={'ollama':'qwen3:8b'}
            monkeypatch.setattr(registry,'probe',lambda *a:{'reachable':False})
            assert factory.for_task(task['task_id']) is first
            with pytest.raises(ValueError,match='different model'):factory.for_task(task['task_id'],('ollama','other'))
        finally:store.delete_task(task['task_id']);factory.close()


def test_native_transport_enforces_context_and_preserves_usage_and_length():
    from delivery_api.provider_http import ParentProviderHTTP
    requests=[]
    def respond(request):
        requests.append(request);body=json.loads(request.content)
        assert request.url.path=='/api/chat'
        assert body['options']=={'num_ctx':32768,'num_predict':12288,'temperature':0.2}
        assert body['think'] is False and body['stream'] is False
        assert body['truncate'] is False and body['shift'] is False
        assert body['messages']==[{'role':'user','content':'a full request'}]
        assert body['format']=={'type':'object'}
        return httpx.Response(200,json={'done':True,'done_reason':'length','message':{'role':'assistant','content':'partial'},'prompt_eval_count':12,'eval_count':12288})
    raw=json.dumps({'model':'qwen3:8b','max_tokens':12288,'temperature':0.2,'messages':[{'role':'user','content':'a full request'}], 'response_format':{'type':'json_schema','json_schema':{'schema':{'type':'object'}}}}).encode()
    with httpx.Client(transport=httpx.MockTransport(respond),follow_redirects=False) as client:
        transport=ParentProviderHTTP(client,'http://ollama.invalid:11434/v1/chat/completions',{},local_context=registry.local_capability('qwen3:8b',registry.QWEN_DIGEST+'a'*52)['context'])
        body,media=transport.send(raw)
        result=json.loads(body);assert result['choices'][0]['finish_reason']=='length'
        assert result['usage']=={'prompt_tokens':12,'completion_tokens':12288,'total_tokens':12300}
        assert media=='application/json'
        bad=json.loads(raw);bad['messages'][0]['content']='x'*40000
        with pytest.raises(ValueError,match='context'):transport.send(json.dumps(bad).encode())
        bad=json.loads(raw);bad['tools']=[]
        with pytest.raises(ValueError,match='Unsupported'):transport.send(json.dumps(bad).encode())
        assert len(requests)==1 # Never dispatch unsupported requests.


def test_ui_selects_exact_qwen_and_start_refuses_unsupported_before_mutation(pg_browser,monkeypatch):
    from types import SimpleNamespace
    from playwright.sync_api import expect
    from delivery_api import app as module,cloud_runtime,model_pins
    from delivery_api.tenancy import tenant_context
    page,base,workspace=pg_browser
    listing=local_listing();listing['installed_models'].insert(0,{'id':'qwen3.5:9b','digest':'56671c2ab938'+'a'*52,'capabilities':['completion','thinking'],'thinking':{'values':[True,False]},'native_context':262144})
    monkeypatch.setattr(registry,'probe',lambda *a:listing)
    monkeypatch.setattr(model_pins.PinRepository,'list',lambda self:[])
    factory=model_pins.DurableGateways(workspace.store,{})
    monkeypatch.setattr(cloud_runtime,'gateway_factory',lambda:factory)
    monkeypatch.setattr(cloud_runtime,'installed',lambda:True)
    monkeypatch.setattr(module,'cloud_execution_admitted',lambda:True)
    previews=[]
    def preview(route,model=None):
        previews.append((route,model))
        return {'route':route,'model_map':{'probing':[route,model]}}
    monkeypatch.setattr(module,'engine_client',SimpleNamespace(preview_route=preview,default_route=lambda:{'route':'ollama','model':'phi4'}))
    with tenant_context(workspace.scope):task=workspace.store.create_task('Choose the installed model')
    page.goto(base+'/tasks/'+task['task_id']+'/view')
    page.locator('[data-start-route=ollama]').click()
    expect(page.locator('#startmodel')).to_have_value('qwen3.5:9b')
    expect(page.locator('#startmodel')).to_be_visible()
    expect(page.locator('#startengine')).to_be_enabled()
    page.screenshot(path='/downloads/registry-qwen-ui-desktop.png',full_page=True)
    page.set_viewport_size({'width':390,'height':844})
    expect(page.locator('#startmodel')).to_be_visible()
    page.evaluate('document.fonts.ready')
    page.wait_for_timeout(150) # Let native select repaint after responsive resize.
    assert page.locator('#startmodel').evaluate('(el)=>getComputedStyle(el).opacity')=='1'
    page.screenshot(path='/downloads/registry-qwen-ui-mobile.png',full_page=True)
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    # Capability preflight rejects a now-incompatible model before any runtime
    # dispatch or immutable pin is written. Keep the honest Start error visible.
    monkeypatch.setenv('VMODEL_LLM_MAX_TOKENS','20000')
    page.locator('#startengine').click()
    expect(page.locator('#startstatus')).to_contain_text('cannot run the full cycle')
    assert not previews
    with tenant_context(workspace.scope):
        assert workspace.store.task(task['task_id'])['state']=='not_started'
        assert workspace.store.task_model_pin(task['task_id'])['environment'] is None


def test_old_server_without_explicit_thinking_or_context_is_not_ready(monkeypatch):
    listing=local_listing()
    for field in ('thinking','native_context','capabilities'):
        old=copy.deepcopy(listing);old['installed_models'][0].pop(field)
        monkeypatch.setattr(registry,'probe',lambda *a:old)
        assert registry.discover([],{})==[]


def test_qwen35_keeps_larger_context_and_model_card_output_envelope():
    value=registry.local_capability('qwen3.5:9b','56671c2ab938'+'a'*52)
    assert value['context']['context_tokens']==131072
    assert value['context']['maximum_output_tokens']==12288
    from delivery_api.ollama_native import prepare
    raw=json.dumps({'model':'qwen3.5:9b','max_tokens':12288,'num_ctx':32768,'messages':[{'role':'user','content':'x'*40000}]}).encode()
    native=prepare(raw,value['context'])
    assert native['options']['num_ctx']==131072
    assert native['think'] is False and len(native['messages'][0]['content'])==40000


def test_remote_profile_never_probes_lists_or_selects_local_even_if_reachable(monkeypatch):
    monkeypatch.setattr(registry,'probe',lambda *a:(_ for _ in ()).throw(AssertionError('remote must not probe local endpoint')))
    local=registry.local_capability('qwen3.5:9b','56671c2ab938'+'a'*52)
    env={'DELIVERY_PUBLIC_ORIGIN':'https://requi.example','OLLAMA_BASE_URL':'http://host.docker.internal:11434/v1'}
    records=registry.discover([{'provider':'ollama','model':'qwen3.5:9b','payload':local,'is_default':True}],env)
    assert records==[]
    with pytest.raises(ValueError,match='not ready'):registry.resolve(records,'ollama','qwen3.5:9b')


def test_remote_model_api_does_not_list_ollama(monkeypatch):
    from types import SimpleNamespace
    from delivery_api import app as module,cloud_runtime
    monkeypatch.setenv('DELIVERY_PUBLIC_ORIGIN','https://requi.example')
    monkeypatch.setenv('DEEPSEEK_API_KEY','test-only')
    monkeypatch.setattr(cloud_runtime,'installed',lambda:True)
    monkeypatch.setattr(cloud_runtime,'gateway_factory',lambda:SimpleNamespace(models={},registry=lambda:[]))
    assert [r['id'] for r in module.model_routes()['routes']]==['deepseek']


@pytest.mark.parametrize('ready',[True,False])
def test_unconfigured_paid_provider_is_not_listed_locally(monkeypatch,ready):
    from types import SimpleNamespace
    from delivery_api import app as module,cloud_runtime
    monkeypatch.setenv('DELIVERY_PUBLIC_ORIGIN','http://localhost:8090')
    monkeypatch.delenv('DEEPSEEK_API_KEY',raising=False)
    monkeypatch.setattr(cloud_runtime,'installed',lambda:ready)
    monkeypatch.setattr(cloud_runtime,'unavailable_reason',lambda:'test_closed')
    monkeypatch.setattr(cloud_runtime,'gateway_factory',lambda:SimpleNamespace(models={},registry=lambda:[]))
    assert [r['id'] for r in module.model_routes()['routes']]==['ollama']


def test_discovery_snapshot_is_bounded_but_start_refreshes_and_paid_errors_stay_closed(store,monkeypatch):
    from delivery_api import model_pins
    from test_production_gateway import row,CEILING
    calls=[]
    monkeypatch.setattr(registry,'probe',lambda *a:calls.append(a) or local_listing())
    paid={'provider':'deepseek','model':row()['model'],'is_default':True,'payload':{'row':row(),'context':dict(CEILING)}}
    monkeypatch.setattr(model_pins.PinRepository,'list',lambda self:[paid])
    factory=model_pins.DurableGateways(store,{'DEEPSEEK_API_KEY':'test-only'})
    try:
        assert len(factory.registry())==2
        assert len(factory.registry())==2 and len(calls)==1
        assert len(factory.refresh_registry())==2 and len(calls)==2
        factory._launch_errors['deepseek']='Vendor terms changed'
        assert [r['provider'] for r in factory.registry()]==['ollama']
        with pytest.raises(ValueError,match='not ready'):registry.resolve(factory.registry(),'deepseek',row()['model'])
        factory._local_snapshot_at=0
        factory.registry();assert len(calls)==3
    finally:factory.close()


def test_approved_native_head_cannot_bypass_missing_live_capabilities(monkeypatch):
    payload=registry.local_capability('qwen3:8b',registry.QWEN_DIGEST+'a'*52)
    head={'provider':'ollama','model':'qwen3:8b','payload':payload,'is_default':True}
    monkeypatch.setattr(registry,'probe',lambda *a:{'reachable':False})
    assert registry.discover([head],{})==[]
