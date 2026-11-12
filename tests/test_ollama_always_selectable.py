"""Selectable is not a readiness or dispatch grant. Validate only on Start."""
from types import SimpleNamespace
import pytest
from delivery_api import app as module,cloud_runtime

@pytest.mark.parametrize('cloud',[True,False])
def test_ollama_disabled_without_runtime_before_preflight(monkeypatch,cloud):
    monkeypatch.setattr(module.config,'CLOUD_TENANTS',cloud)
    monkeypatch.setattr(module,'cloud_execution_admitted',lambda:False)
    monkeypatch.setattr(cloud_runtime,'installed',lambda:False)
    monkeypatch.setattr(cloud_runtime,'gateway_factory',lambda:None)
    monkeypatch.setattr(cloud_runtime,'install',lambda *a,**kw:None)
    monkeypatch.setattr(module,'engine_client',SimpleNamespace(default_route=lambda:None,
        preview_route=lambda route:(_ for _ in ()).throw(module.EngineUnreachableError('fixture',RuntimeError('not running'))),
        local_models=lambda:(_ for _ in ()).throw(module.EngineUnreachableError('fixture',RuntimeError('not running')))))
    result=module.model_routes()
    local=next(r for r in result['routes'] if r['id']=='ollama')
    assert local['available'] is False
    assert local['reason']=='runtime_unavailable'


def test_child_preflight_preserves_model_missing_cause(monkeypatch,tmp_path):
    from delivery_api.child_interview import ChildInterviewClient
    from interview_engine import session_models
    from interview_engine.errors import UserFacingError
    client=ChildInterviewClient(object(),tmp_path,gateway_factory=None,dsn='unused',endpoint='http://fixture')
    def missing(*args,**kwargs):raise UserFacingError('Ollama model fixture is not installed.','The requested model was not found.','Pull fixture, then retry.')
    monkeypatch.setattr(session_models,'validate',missing)
    with pytest.raises(module.EngineClientError) as error:client.preview_route('ollama')
    assert error.value.http_status==422
    assert 'not installed' in error.value.error.detail and 'Pull fixture' in error.value.error.detail


def test_signed_in_start_keeps_ollama_enabled_then_shows_actual_failure(pg_browser,monkeypatch):
    from playwright.sync_api import expect
    from delivery_api.tenancy import tenant_context
    from vmodel_contracts.interactive import ProtocolError,ProtocolErrorCode
    page,base,workspace=pg_browser
    with tenant_context(workspace.scope):tid=workspace.store.create_task('Local connection fixture')['task_id']
    def missing(route):
        assert route=='ollama'
        raise module.EngineClientError(422,ProtocolError(code=ProtocolErrorCode.INVALID_RESPONSE,
            detail='Ollama model fixture is not installed. Pull fixture, then retry.'))
    monkeypatch.setattr(module,'engine_client',SimpleNamespace(default_route=lambda:None,preview_route=missing))
    # The fixture installs only a failing preflight adapter, never a dispatcher.
    monkeypatch.setattr(cloud_runtime,'installed',lambda:True)
    monkeypatch.setattr(module,'cloud_execution_admitted',lambda:True)
    monkeypatch.setattr(cloud_runtime,'gateway_factory',lambda:None)
    page.goto(base+'/tasks/'+tid+'/view')
    local=page.locator('[data-start-route=ollama]');expect(local).to_be_enabled();local.click()
    expect(page.locator('#startengine')).to_be_enabled();page.locator('#startengine').click()
    expect(page.locator('#startstatus')).to_contain_text('Ollama model fixture is not installed')
    expect(page.locator('#startengine')).to_be_enabled()
    page.screenshot(path='/tmp/requi-test-artifacts/ollama-selectable-error-on-start.png',full_page=True)
    with tenant_context(workspace.scope):assert workspace.store.task(tid)['state']=='not_started'
