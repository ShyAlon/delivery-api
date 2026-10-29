from fastapi.testclient import TestClient

from delivery_api import auth, config


def test_run_start_limiter_does_not_rate_limit_interview_answers(monkeypatch,pg_workspace_factory,pg_task_app):
    from delivery_api.tenancy import tenant_context
    # Test only admitted request dependency ordering,no engine/model execution.
    from delivery_api import cloud_runtime
    monkeypatch.setattr(cloud_runtime,"installed",lambda:True)
    monkeypatch.setattr(auth,"limiter",auth.RateLimiter())
    monkeypatch.setattr(config,"RUN_START_RATE_LIMIT",1)
    with pg_workspace_factory() as workspace:
        with tenant_context(workspace.scope):
            task=workspace.store.create_task("Answer rate scope")["task_id"]
        c=TestClient(pg_task_app(workspace))
        from delivery_api import app as app_module
        monkeypatch.setattr(app_module.runner,"input_state",lambda _:{"decision":None,"process_live":False,"waiting":False})
        # A stale answer reaches its domain conflict repeatedly,never 429.
        for _ in range(3):
            r=c.post(f"/api/tasks/{task}/answer",json={"answer":"same"},headers=workspace.headers)
            assert r.status_code==409


def test_run_start_dependency_rate_limits_actual_starts(monkeypatch,pg_workspace_factory,pg_task_app):
    # Test only admitted request dependency ordering,no engine/model execution.
    from delivery_api import cloud_runtime
    monkeypatch.setattr(cloud_runtime,"installed",lambda:True)
    monkeypatch.setattr(auth,"limiter",auth.RateLimiter())
    monkeypatch.setattr(config,"RUN_START_RATE_LIMIT",1)
    with pg_workspace_factory() as workspace:
        c=TestClient(pg_task_app(workspace))
        # Invalid task IDs prove dependency bucket ownership without model work.
        first=c.post("/api/tasks/missing/start",json={"model_route":"ollama"},headers=workspace.headers)
        second=c.post("/api/tasks/missing/start",json={"model_route":"ollama"},headers=workspace.headers)
        assert first.status_code==404
        assert second.status_code==429 and "run-start" in second.text


def test_rate_limit_contracts_require_signed_pg_fixture():
    import inspect
    for case in (test_run_start_limiter_does_not_rate_limit_interview_answers,
                 test_run_start_dependency_rate_limits_actual_starts):
        assert 'pg_workspace_factory' in inspect.signature(case).parameters
        assert 'pg_task_app' in inspect.signature(case).parameters


def test_pg_rate_contract_setup_preserves_closed_runtime_boundary(monkeypatch,pg_workspace_factory,pg_task_app):
    from delivery_api import cloud_runtime
    monkeypatch.setattr(cloud_runtime,'installed',lambda:False)
    with pg_workspace_factory() as workspace:
        c=TestClient(pg_task_app(workspace))
        for endpoint in ('start','answer'):
            r=c.post('/api/tasks/missing/'+endpoint,json={},headers=workspace.headers)
            assert r.status_code==503
            assert r.json()['detail']=='Cloud execution pending scoped worker protocol'
        monkeypatch.setattr(cloud_runtime,'installed',lambda:True)
        for endpoint in ('start','answer'):
            assert c.post('/api/tasks/missing/'+endpoint,json={}).status_code==401
