
import json
import pytest


@pytest.fixture(autouse=True)
def model_workspace(pg_workspace_factory,monkeypatch):
    """Direct helper contracts keep their policy mode; storage is real tenant PG."""
    from delivery_api import app as module
    # Direct file-backed provenance/helper contracts select their policy explicitly.
    monkeypatch.setattr(module.config,'CLOUD_TENANTS',False)
    from pg_storage_guard import assert_pg_storage_only
    from delivery_api.tenancy import tenant_context
    with pg_workspace_factory() as workspace:
        monkeypatch.setattr(module,'store',workspace.store)
        monkeypatch.setitem(module.app.state._state,'store',workspace.store)
        monkeypatch.setattr(module.orchestrator,'store',workspace.store)
        assert_pg_storage_only()
        with tenant_context(workspace.scope):
            yield workspace


def test_route_value_default_and_env_override(monkeypatch):
    from delivery_api import model_routes
    monkeypatch.delenv("DELIVERY_OLLAMA_MODEL", raising=False)
    assert model_routes.route_value("ollama") == "ollama:phi4"
    assert model_routes.route_value("deepseek") == "deepseek:deepseek-flash"
    monkeypatch.setenv("DELIVERY_OLLAMA_MODEL", "phi4")
    assert model_routes.route_value("ollama") == "ollama:phi4"



def _seed_task(module, store, task_id):
    if store.task(task_id):
        store.delete_task(task_id)
    store.create_task("Provenance " + task_id, "An idea", task_id=task_id)
    store.add_event(task_id, "waiting_input", "Interviewer: Q1?", phase_key="intent",
                    payload={"question": "Q1?"})
    store.add_event(task_id, "answer", "You: A1", phase_key="intent",
                    payload={"answer": "A1", "transcript": True})


def test_transcript_header_from_usage_jsonl(monkeypatch, tmp_path):
    from delivery_api import app as module
    store = module.store
    task_id = "provenance-usage-test"
    _seed_task(module, store, task_id)
    store.set_engine_session(task_id, "s-prov")
    from test_http_cutover import _PGUsage
    usage=_PGUsage(store,task_id,'interview/usage.jsonl')
    records = [
        {"session_id": "s-prov", "stage": "probing", "provider": "ollama", "model": "phi4",
         "prompt_tokens": 1, "completion_tokens": 1, "seconds": 0.1, "cost_usd": 0.0},
        {"session_id": "s-prov", "stage": "distillation", "provider": "ollama", "model": "phi4",
         "prompt_tokens": 1, "completion_tokens": 1, "seconds": 0.1, "cost_usd": 0.0},
    ]
    usage.write_text("".join(json.dumps(r) + "\n" for r in records))
    monkeypatch.setenv("VMODEL_ARTIFACTS_DIR", str(tmp_path))
    text = module._interview_transcript_markdown(task_id)
    assert text.splitlines()[0].startswith("Model segment unassigned: ollama:phi4")
    assert "2 calls" in text.splitlines()[0]
    assert "## Interview questions and answers" in text
    store.delete_task(task_id)


def test_transcript_header_mixed_models_lists_stages(monkeypatch, tmp_path):
    from delivery_api import app as module
    store = module.store
    task_id = "provenance-mixed-test"
    _seed_task(module, store, task_id)
    store.set_engine_session(task_id, "s-mixed")
    from test_http_cutover import _PGUsage
    usage=_PGUsage(store,task_id,'interview/usage.jsonl')
    records = [
        {"session_id": "s-mixed", "stage": "probing", "provider": "ollama", "model": "phi4",
         "prompt_tokens": 1, "completion_tokens": 1, "seconds": 0.1, "cost_usd": 0.0},
        {"session_id": "s-mixed", "stage": "distillation", "provider": "ollama", "model": "qwen2.5-coder:14b",
         "prompt_tokens": 1, "completion_tokens": 1, "seconds": 0.1, "cost_usd": 0.0},
    ]
    usage.write_text("".join(json.dumps(r) + "\n" for r in records))
    monkeypatch.setenv("VMODEL_ARTIFACTS_DIR", str(tmp_path))
    lines = module._interview_transcript_markdown(task_id).splitlines()
    assert any(line.startswith("Model segment unassigned: ollama:qwen2.5-coder:14b") for line in lines)
    assert any(line.startswith("Model segment unassigned: ollama:phi4") for line in lines)
    store.delete_task(task_id)


def test_transcript_header_falls_back_to_resolved_route_event(monkeypatch, tmp_path):
    from delivery_api import app as module
    store = module.store
    task_id = "provenance-fallback-test"
    _seed_task(module, store, task_id)
    store.set_engine_session(task_id, "s-missing-artifacts")
    store.add_event(task_id, "model_route", "interview uses ollama:phi4", phase_key="intent",
                    payload={"route": "ollama", "resolved": "ollama:phi4", "model": "phi4", "scope": "start"})
    monkeypatch.setenv("VMODEL_ARTIFACTS_DIR", str(tmp_path))  # no usage.jsonl on disk
    text = module._interview_transcript_markdown(task_id)
    assert text.splitlines()[0] == "Model: ollama:phi4"
    store.delete_task(task_id)


def test_new_task_defaults_to_deepseek():
    from delivery_api import app as module
    task=module.store.create_task("New default DeepSeek",task_id="new-deepseek-test")
    try:assert task["model_route"]=="deepseek"
    finally:module.store.delete_task(task["task_id"])

def test_unknown_route_is_rejected():
    from delivery_api import model_routes
    import pytest
    with pytest.raises(ValueError,match="unsupported"):
        model_routes.route_value("typo")

def test_execution_payload_requires_debug_and_uses_pinned_state(monkeypatch,tmp_path,model_workspace):
    from delivery_api import app as module
    task_id='execution-debug-test'
    _seed_task(module,module.store,task_id)
    module.store.set_engine_session(task_id,'s-debug')
    from delivery_api.operator_reads import OperatorReadStore
    from delivery_api.operator_authorization import OperatorPrincipal
    ws=model_workspace
    with ws.admin() as connection:
        connection.execute('INSERT INTO operator_principals VALUES(%s,%s,%s,true)',(ws.scope.owner_id,'model-debug',['debug.read']))
    monkeypatch.setattr(module.app.state,'operator_read_store',OperatorReadStore(ws.dsn.replace('user=sandbox','user=delivery_app'),OperatorPrincipal(ws.scope.owner_id,'model-debug',frozenset({'debug.read'}))),raising=False)
    from delivery_api.generator_documents import GeneratorDocuments
    docs={
      'interview/usage.jsonl':(json.dumps({'stage':'probing','provider':'deepseek','model':'deepseek-chat'})+'\n').encode(),
      'interview/llm-calls.jsonl':(json.dumps({'event':'llm_call_error','model':'deepseek-chat','error':{'kind':'empty_response','raw_payload':'sensitive response'}})+'\n').encode()}
    monkeypatch.setattr(GeneratorDocuments,'owner_reader',lambda *a:type('Reader',(),{'read':staticmethod(docs.get)})())
    root=tmp_path/'s-debug'/'interview';root.mkdir(parents=True)
    (root/'http-session.json').write_text(json.dumps({'model_map':{'probing':['deepseek','deepseek-chat']}}))
    (root/'usage.jsonl').write_text(json.dumps({'stage':'probing','provider':'deepseek','model':'deepseek-chat'})+'\n')
    (root/'llm-calls.jsonl').write_text(json.dumps({'event':'llm_call_error','model':'deepseek-chat','error':{'kind':'empty_response','raw_payload':'sensitive response'}})+'\n')
    (root/'debug').mkdir();(root/'debug'/'probing-answer.raw.txt').write_text('private raw response')
    monkeypatch.setenv('VMODEL_ARTIFACTS_DIR',str(tmp_path))
    try:
        normal=module.task_execution(task_id,debug=False,a=None)
        assert normal['effective_models']['probing']==['deepseek','deepseek-chat']
        assert normal['calls'][0]['model']=='deepseek-chat'
        assert normal['raw']==[]
        assert 'raw_payload' not in str(normal['calls'])
        debug=module.task_execution(task_id,debug=True,a=None)
        assert debug['raw'][0]['content']=='private raw response'
        assert debug['calls'][0]['error']['raw_payload']=='sensitive response'
    finally:
        module.store.delete_task(task_id)
        with ws.admin() as connection:connection.execute('DELETE FROM operator_principals WHERE subject=%s',(ws.scope.owner_id,))


def test_needs_default_comes_from_generator_config_not_delivery_ollama_override(monkeypatch,tmp_path):
 from delivery_api import model_routes
 generator=tmp_path/'spec-generator';generator.mkdir()
 (tmp_path/'.env').write_text('SPEC_GENERATOR_MODEL_NEEDS=ollama:phi4\n')
 (generator/'.env').write_text('SPEC_GENERATOR_MODEL_NEEDS=ollama:gemma3\n')
 monkeypatch.setenv('SPEC_GENERATOR_DIR',str(generator))
 monkeypatch.setenv('DELIVERY_OLLAMA_MODEL','qwen2.5-coder:14b')
 monkeypatch.delenv('SPEC_GENERATOR_MODEL_NEEDS',raising=False)
 assert model_routes.needs_initial_model()=='ollama:gemma3'
 monkeypatch.setenv('SPEC_GENERATOR_MODEL_NEEDS','ollama:phi4')
 assert model_routes.needs_initial_model()=='ollama:phi4'


def test_generator_phase_default_ignores_interview_route(monkeypatch,tmp_path):
    from delivery_api import model_routes
    repo=tmp_path/'spec-generator';repo.mkdir()
    (repo/'.env').write_text('SPEC_GENERATOR_MODEL_SRS=ollama:phi4\nSPEC_GENERATOR_MODEL_NEEDS=ollama:qwen\n')
    monkeypatch.setenv('SPEC_GENERATOR_DIR',str(repo))
    monkeypatch.delenv('SPEC_GENERATOR_MODEL_SRS',raising=False)
    # An earlier import of delivery_api.config can load a developer's .env into os.environ.
    monkeypatch.delenv('SPEC_GENERATOR_MODEL_ARCHITECTURE',raising=False)
    assert model_routes.phase_initial_model('srs')=='ollama:phi4'
    assert model_routes.phase_initial_model('architecture')=='ollama:qwen2.5-coder:14b'
    monkeypatch.setenv('SPEC_GENERATOR_MODEL_SRS','deepseek:deepseek-chat')
    assert model_routes.phase_initial_model('srs')=='deepseek:deepseek-chat'


def test_task_default_exact_selection_overrides_each_stage_environment(monkeypatch,tmp_path,model_workspace):
    from delivery_api import model_routes
    from delivery_api.postgres_store import PostgresStore
    store=model_workspace.store;tid=store.create_task('Exact route')['task_id']
    monkeypatch.setenv('SPEC_GENERATOR_MODEL_NEEDS','ollama:phi4')
    monkeypatch.setenv('SPEC_GENERATOR_MODEL_ARCHITECTURE','ollama:qwen2.5-coder:14b')
    store.set_model_default(tid,'deepseek:deepseek-flash')
    assert model_routes.task_default_model(store,tid,'needs')==('deepseek:deepseek-flash','selected')
    assert model_routes.task_default_model(store,tid,'architecture')==('deepseek:deepseek-flash','selected')
    reopened=PostgresStore(model_workspace.dsn.replace('user=sandbox','user=delivery_app'))
    assert model_routes.task_default_model(reopened,tid,'srs')[0]=='deepseek:deepseek-flash'
    reopened.set_model_default(tid,'ollama:phi4')
    assert model_routes.task_default_model(reopened,tid,'architecture')[0]=='ollama:phi4'


def test_legacy_default_uses_recorded_selection_but_not_phase_execution(monkeypatch,tmp_path,model_workspace):
    from delivery_api import model_routes
    store=model_workspace.store;tid=store.create_task('Legacy route')['task_id']
    monkeypatch.setenv('SPEC_GENERATOR_MODEL_SRS','ollama:qwen')
    store.add_event(tid,'model_route','old generator ran',phase_key='needs',payload={'resolved':'ollama:phi4','mode':'start'})
    assert model_routes.task_default_model(store,tid,'srs')==('ollama:qwen','legacy generator configuration (no recorded exact selection)')
    store.add_event(tid,'model_route','interview selected',phase_key='intent',payload={'resolved':'deepseek:deepseek-flash','scope':'start'})
    assert model_routes.task_default_model(store,tid,'srs')==('deepseek:deepseek-flash','previous selection')


def test_bound_legacy_session_without_exact_selection_requires_a_choice(tmp_path,model_workspace):
    import pytest
    from delivery_api import model_routes
    store=model_workspace.store;tid=store.create_task('Old session')['task_id']
    store.set_engine_session(tid,'old-session')
    store.add_event(tid,'model_route','generator used legacy configured model',phase_key='needs',payload={'resolved':'ollama:qwen','mode':'start'})
    with pytest.raises(ValueError,match='choose a model'):
        model_routes.task_default_model(store,tid,'srs')


def test_route_listing_does_not_probe_local_models_before_start(monkeypatch):
    from delivery_api import app as module
    from types import SimpleNamespace
    monkeypatch.setattr(module.config,'CLOUD_TENANTS',False)
    def preview(route,**kw):
        assert route=='deepseek'
        return {'model_map':{'probing':[route,'pinned']}}
    def unavailable():raise AssertionError('local model list must not be queried')
    monkeypatch.setattr(module,'engine_client',SimpleNamespace(default_route=lambda:{'route':'ollama','model':'pinned'},preview_route=preview,local_models=unavailable))
    result=module.model_routes()
    route=next(r for r in result['routes'] if r['id']=='ollama')
    assert route['available'] and route['models']==['pinned']
    assert route['readiness']=='checked_on_start'


def test_model_route_fixture_uses_restricted_pg_and_rejects_sqlite():
    import pytest
    from delivery_api import app as module
    from delivery_api.postgres_store import PostgresStore
    from pg_storage_guard import assert_pg_storage_only
    assert isinstance(module.store,PostgresStore)
    with module.store._postgres.transaction() as connection:
        role=connection.execute('SELECT current_user').fetchone()['current_user']
    assert role=='requi_backend'
    assert_pg_storage_only()
