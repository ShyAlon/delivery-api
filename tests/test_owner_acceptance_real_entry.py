"""Actual signed-in HTTP, real child graph and disposable PG; fixture vendor only."""
from test_fresh_start_acceptance import test_fresh_zero_credit_http_start as run_fresh_start


def test_owner_script_real_http_child_cycle_with_fixture_provider(pg_workspace_factory,pg_task_app,pg_runtime_environment,monkeypatch,tmp_path):
    run_fresh_start(pg_workspace_factory,pg_task_app,pg_runtime_environment,monkeypatch,tmp_path,True,owner_acceptance=True)
