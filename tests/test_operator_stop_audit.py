"""Operator stop audit against the real Postgres: the migration, the read-only role, and the route that serves the page."""
import json
import time
import uuid

import psycopg
import pytest

from test_postgres_store import DSN, A, B
from delivery_api.operator_authorization import OperatorPrincipal
from delivery_api.operator_reads import OperatorReadStore
from delivery_api.tenancy import TenantAccessDenied, tenant_context

pytestmark = pytest.mark.skipif(not DSN, reason="disposable migrated Postgres required")
APP = DSN.replace("user=sandbox", "user=delivery_app") if DSN else None
CAPS = frozenset({"logs.read", "sessions.read"})


@pytest.fixture
def stops(store):
    """Three audit rows in two workspaces (an operator stop, an owner stop, an unrelated entry) and an enabled operator row for owner A."""
    tag = uuid.uuid4().hex[:8]
    with tenant_context(A):
        store.audit("op-" + tag, "run.stopped", None, {"by": "operator", "peak_rss_mb": 412.5, "phase": "needs"})
        store.audit("someone", "task.renamed", None, {"x": 1})
    with tenant_context(B):
        store.audit("b-label", "run.stopped", None, {"by": "owner", "peak_rss_mb": 80.0})
    with psycopg.connect(DSN) as c:
        c.execute("UPDATE public.operator_principals SET enabled=false")
        c.execute("INSERT INTO public.operator_principals(subject,registry_version,capabilities,enabled) VALUES (%s,'ops-v1',%s,true) "
                  "ON CONFLICT (subject) DO UPDATE SET enabled=true,registry_version='ops-v1',capabilities=EXCLUDED.capabilities",
                  (A.owner_id, ["analytics.read", "sessions.read", "debug.read", "logs.read"]))
    yield store, tag
    with psycopg.connect(DSN) as c:
        c.execute("DELETE FROM public.operator_principals WHERE subject=%s", (A.owner_id,))   # leave no operator row behind for other tests


def read(store, owner=A, caps=CAPS, version="ops-v1"):
    reader = OperatorReadStore(APP, OperatorPrincipal(owner.owner_id, version, caps))
    with tenant_context(owner):
        return reader.stops(store)


def test_the_operator_sees_every_stop_from_both_workspaces_and_nothing_else(stops):
    store, tag = stops
    out = read(store)
    rows = [r for r in out["stops"] if r["operator"] == "op-" + tag or r["owner"] and r["peak_memory_mb"] == 80.0]
    kinds = {(r["stopped_by"], r["phase"], r["peak_memory_mb"]) for r in rows}
    assert ("Operator", "needs", 412.5) in kinds and ("Owner", None, 80.0) in kinds
    assert all(set(r) == {"when", "stopped_by", "operator", "owner", "phase", "peak_memory_mb"} for r in out["stops"])   # no ids, no task titles
    assert not any("task.renamed" in json.dumps(r) for r in out["stops"]) and out["mutations_available"] is False
    assert [r["operator"] for r in out["stops"] if r["stopped_by"] == "Owner"] == [None] * len([1 for r in out["stops"] if r["stopped_by"] == "Owner"])


def test_the_operator_role_reads_only_run_stopped_rows_straight_from_the_database(stops):
    with psycopg.connect(APP) as c:
        c.execute("BEGIN")
        c.execute("SELECT set_config('request.jwt.claim.sub',%s,true)", (A.owner_id,))
        c.execute("SET LOCAL ROLE requi_operator_read")
        rows = c.execute("SELECT DISTINCT action FROM public.audit_log").fetchall()
        c.execute("ROLLBACK")
    assert {r[0] for r in rows} <= {"run.stopped"}


def test_a_user_who_is_not_the_registered_operator_is_denied_by_the_database(stops):
    store, _ = stops
    with pytest.raises(TenantAccessDenied):read(store, owner=B)
    with psycopg.connect(DSN) as c:
        c.execute("UPDATE public.operator_principals SET enabled=false")
    with pytest.raises(TenantAccessDenied):read(store)
    with pytest.raises(TenantAccessDenied):read(store, caps=frozenset({"sessions.read"}))


def test_the_migration_registers_the_named_account_refuses_a_second_operator_and_is_repeatable():
    import pathlib
    sql = next(pathlib.Path(__file__).resolve().parents[1].glob("supabase/migrations/20261020130000_operator_stop_audit.sql")).read_text()
    shy, other = str(uuid.uuid4()), str(uuid.uuid4())
    with psycopg.connect(DSN, autocommit=True) as c:
        c.execute("UPDATE public.operator_principals SET enabled=false")
        c.execute("INSERT INTO auth.users(id,email) VALUES (%s,'shy.alon@gmail.com'),(%s,'other@example.com')", (shy, other))
        try:
            c.execute(sql);c.execute(sql)   # twice: repeatable
            row = c.execute("SELECT enabled,registry_version,capabilities FROM public.operator_principals WHERE subject=%s", (shy,)).fetchone()
            assert row[0] is True and row[1] == "ops-v1" and "logs.read" in row[2]
            c.execute("UPDATE public.operator_principals SET enabled=false WHERE subject=%s", (shy,))
            c.execute("INSERT INTO public.operator_principals(subject,registry_version,capabilities,enabled) VALUES (%s,'v','{analytics.read}',true)", (other,))
            with pytest.raises(psycopg.errors.RaiseException, match="different operator"):c.execute(sql)
            assert c.execute("SELECT enabled FROM public.operator_principals WHERE subject=%s", (shy,)).fetchone()[0] is False   # nothing changed
            c.execute("DELETE FROM public.operator_principals WHERE subject=%s", (other,))
            c.execute("DELETE FROM public.operator_principals WHERE subject=%s", (shy,))
            c.execute("DELETE FROM public.users WHERE user_id=%s", (shy,))   # the signup trigger made a profile row
            c.execute("DELETE FROM auth.users WHERE id=%s AND email='shy.alon@gmail.com'", (shy,))
            with pytest.raises(psycopg.errors.RaiseException, match="No Requi.re account has the email"):c.execute(sql)   # accounts exist, none is his
        finally:
            c.execute("DELETE FROM public.operator_principals WHERE subject IN (%s,%s)", (shy, other))
            c.execute("DELETE FROM public.users WHERE user_id IN (%s,%s)", (shy, other))
            c.execute("DELETE FROM auth.users WHERE id IN (%s,%s)", (shy, other))


from test_cloud_api import cloud, header  # noqa: E402,F401


def test_the_stops_route_serves_the_operator_refuses_others_and_says_what_to_do_when_not_switched_on(cloud, stops, monkeypatch):
    import delivery_api.app as module
    from delivery_api import auth, config
    client, store, _ = cloud
    path = "/api/admin/operations/stops"
    monkeypatch.setattr(config, "DATABASE_URL", APP)
    monkeypatch.setattr(module.app.state,'operator_read_store',OperatorReadStore(APP,OperatorPrincipal(A.owner_id,'ops-v1',frozenset({'logs.read','sessions.read'}))),raising=False)
    assert client.get(path).status_code == 401
    assert client.get(path, headers=header("a")).status_code == 403   # signed in, not an administrator
    module.app.dependency_overrides[auth.require_admin] = lambda: auth.Actor(A.owner_id, "Shy", "shy.alon@gmail.com", True)
    try:
        ok = client.get(path, headers=header("a"))
        assert ok.status_code == 200 and any(r["stopped_by"] == "Operator" and r["phase"] == "needs" for r in ok.json()["stops"])
        with psycopg.connect(DSN) as c:
            c.execute("UPDATE public.operator_principals SET enabled=false")
        off = client.get(path, headers=header("a"))
        assert off.status_code == 503
        detail = off.json()["detail"]
        assert "not switched on" in detail["error"] and "20261020130000_operator_stop_audit.sql" in detail["next"]
    finally:
        module.app.dependency_overrides.pop(auth.require_admin, None)
