"""Operator credit grants against the real Postgres: the migration, the two database functions, the role, and the routes."""
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
CAPS = frozenset({"analytics.read", "credits.allocate"})
SCALE = 10**9


def register(enabled=True, caps=("analytics.read", "credits.allocate")):
    with psycopg.connect(DSN, autocommit=True) as c:
        c.execute("UPDATE public.operator_principals SET enabled=false")
        c.execute("INSERT INTO public.operator_principals(subject,registry_version,capabilities,enabled) VALUES (%s,'ops-v1',%s,%s) "
                  "ON CONFLICT (subject) DO UPDATE SET enabled=EXCLUDED.enabled,registry_version='ops-v1',capabilities=EXCLUDED.capabilities",
                  (A.owner_id, list(caps), enabled))


@pytest.fixture
def world(store):
    """An enabled operator (owner A) and a fresh account with no credits yet."""
    register()
    oid, email = str(uuid.uuid4()), "grant-%s@example.test" % uuid.uuid4().hex[:8]
    with psycopg.connect(DSN, autocommit=True) as c:
        c.execute("INSERT INTO auth.users(id,email,email_confirmed_at) VALUES(%s,%s,now())", (oid, email))
    yield store, oid, email
    with psycopg.connect(DSN, autocommit=True) as c:
        c.execute("DELETE FROM public.operator_principals WHERE subject=%s", (A.owner_id,))   # leave no operator row behind for other tests
        c.execute("DELETE FROM public.admission_limits WHERE owner_id IN (%s,%s)", (oid, A.owner_id))
        c.execute("DELETE FROM credit_accounts WHERE owner_id IN (%s,%s)", (oid, A.owner_id))


def reader(owner=A, caps=CAPS):
    return OperatorReadStore(APP, OperatorPrincipal(owner.owner_id, "ops-v1", caps))


def rows(sql, *args):
    with psycopg.connect(DSN) as c:
        return c.execute(sql, args).fetchall()


def test_a_grant_writes_evidence_ledger_balance_and_the_standard_admission_row_together(world):
    store, oid, email = world
    with tenant_context(A):
        out = reader().grant_apply(store, email.upper(), 50, "Free launch credits")
    assert out == {"granted": True, "before_credits": 0, "after_credits": 50, "admission_row": "created standard row"}
    assert rows("SELECT balance_units FROM credit_accounts WHERE owner_id=%s", oid) == [(50 * SCALE,)]
    assert rows("SELECT kind,delta_units,actor,reason FROM credit_ledger WHERE owner_id=%s", oid) == [("grant", 50 * SCALE, "operator:" + A.owner_id, "Free launch credits")]
    adj = rows("SELECT operator_subject::text,delta_units,before_balance_units,after_balance_units,reason FROM credit_adjustments WHERE owner_id=%s", oid)
    assert adj == [(A.owner_id, 50 * SCALE, 0, 50 * SCALE, "Free launch credits")]
    assert rows("SELECT concurrency_max,expense_limit FROM admission_limits WHERE owner_id=%s", oid) == [(3, None)]   # 3 runs at once, no spend cap


def test_the_same_grant_twice_on_one_day_changes_nothing_the_second_time(world):
    store, oid, email = world
    with tenant_context(A):
        assert reader().grant_apply(store, email, 10, "first")["granted"] is True
        again = reader().grant_apply(store, email, 10, "first")
        other = reader().grant_apply(store, email, 12.5, "a different amount is a different grant")
    assert again == {"granted": False, "already_granted_today": True} and other["after_credits"] == 22.5
    assert rows("SELECT count(*) FROM credit_ledger WHERE owner_id=%s", oid) == [(2,)]


def test_the_operators_own_first_grant_creates_the_two_dollar_limit(world):
    store, oid, email = world
    with psycopg.connect(DSN, autocommit=True) as c:
        c.execute("DELETE FROM public.admission_limits WHERE owner_id=%s", (A.owner_id,))
        c.execute("INSERT INTO credit_accounts(owner_id,balance_units,units_per_credit,updated_at) VALUES(%s,0,%s,0) ON CONFLICT (owner_id) DO NOTHING", (A.owner_id, SCALE))
        own_email = c.execute("SELECT email FROM auth.users WHERE id=%s", (A.owner_id,)).fetchone()[0]
    with tenant_context(A):
        out = reader().grant_apply(store, own_email, 7.25, "own credits %s" % uuid.uuid4().hex[:6])
    assert out["admission_row"] == "created own row with the 2 dollar 24 hour limit"
    assert rows("SELECT concurrency_max,expense_limit FROM admission_limits WHERE owner_id=%s", A.owner_id) == [(3, 2 * 10**12)]


def test_preview_changes_nothing_and_says_what_would_happen(world):
    store, oid, email = world
    with tenant_context(A):
        p = reader().grant_preview(store, email, 20)
        missing = reader().grant_preview(store, "nobody-%s@example.test" % uuid.uuid4().hex, 20)
    assert p["found"] is True and p["balance_credits"] == 0 and p["after_credits"] == 20 and p["already_granted_today"] is False and p["admission_row"] == "create standard row"
    assert missing == {"found": False}
    assert rows("SELECT count(*) FROM credit_ledger WHERE owner_id=%s", oid) == [(0,)] and rows("SELECT count(*) FROM credit_accounts WHERE owner_id=%s", oid) == [(0,)]


@pytest.mark.parametrize("credits", [0, -5, 500.01, 1000, "abc", float("nan"), 0.0000001])
def test_bad_amounts_are_refused_before_anything_is_written(world, credits):
    store, oid, email = world
    with tenant_context(A):
        with pytest.raises(ValueError):
            reader().grant_apply(store, email, credits, "x")
    assert rows("SELECT count(*) FROM credit_ledger WHERE owner_id=%s", oid) == [(0,)]


def test_the_ceiling_is_also_enforced_by_the_database_and_500_is_allowed(world):
    store, oid, email = world
    with psycopg.connect(APP) as c:
        c.execute("SELECT set_config('request.jwt.claim.sub',%s,true)", (A.owner_id,))
        c.execute("SET LOCAL ROLE requi_operator_grant")
        with pytest.raises(psycopg.errors.RaiseException):
            c.execute("SELECT public.requi_operator_grant_credits(%s,501,'x')", (email,))
    with tenant_context(A):
        assert reader().grant_apply(store, email, 500, "the ceiling")["after_credits"] == 500


def test_unknown_email_reason_missing_and_denied_callers_are_refused(world):
    store, oid, email = world
    with tenant_context(A):
        with pytest.raises(psycopg.errors.RaiseException, match="no account"):
            reader().grant_apply(store, "nobody-%s@example.test" % uuid.uuid4().hex, 5, "x")
        with pytest.raises(ValueError):
            reader().grant_apply(store, email, 5, "   ")
        with pytest.raises(TenantAccessDenied):
            reader(caps=frozenset({"analytics.read"})).grant_apply(store, email, 5, "x")   # missing capability
    with tenant_context(B):
        with pytest.raises(psycopg.errors.RaiseException, match="operator denied"):
            reader(owner=B).grant_apply(store, email, 5, "x")   # the app is fooled, the database registry is not
    register(enabled=False)
    with tenant_context(A):
        with pytest.raises(psycopg.errors.RaiseException, match="operator denied"):
            reader().grant_apply(store, email, 5, "x")   # the database registry says no even though the app thinks yes
    register(caps=("analytics.read", "logs.read"))
    with tenant_context(A):
        with pytest.raises(psycopg.errors.RaiseException, match="operator denied"):
            reader().grant_apply(store, email, 5, "x")   # registered but without the grant capability
    assert rows("SELECT count(*) FROM credit_ledger WHERE owner_id=%s", oid) == [(0,)]


def test_the_grant_role_can_only_call_the_two_functions(world):
    store, oid, email = world
    with psycopg.connect(APP) as c:
        c.execute("SET LOCAL ROLE requi_operator_grant")
        for sql in ("UPDATE public.credit_accounts SET balance_units=1", "SELECT * FROM public.credit_adjustments", "INSERT INTO public.admission_limits(owner_id,config_version,period_seconds,rate_window_seconds,concurrency_max) VALUES (gen_random_uuid(),'x',1,1,1)"):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                c.execute(sql)
            c.rollback()
            c.execute("SET LOCAL ROLE requi_operator_grant")


def test_recent_grants_list_shows_who_what_and_why(world):
    store, oid, email = world
    with tenant_context(A):
        reader().grant_apply(store, email, 5, "listed %s" % oid[:6])
        out = reader().grants(store)["grants"]
    row = next(g for g in out if g["reason"] == "listed " + oid[:6])
    assert row["credits"] == 5 and row["by"] == "Operator" and set(row) == {"when", "account", "credits", "reason", "by"}


def test_the_migration_is_repeatable_and_the_registry_accepts_the_new_capability():
    import pathlib
    sql = next(pathlib.Path(__file__).resolve().parents[1].glob("supabase/migrations/20261020150000_operator_grant_credits.sql")).read_text()
    subject = str(uuid.uuid4())
    with psycopg.connect(DSN, autocommit=True) as c:
        # Never leave the shared server at the historical constraint version.
        with c.transaction(force_rollback=True):
            c.execute("UPDATE public.operator_principals SET enabled=false")
            c.execute("INSERT INTO auth.users(id,email) VALUES (%s,%s)", (subject, "mig-%s@example.test" % subject[:8]))
            c.execute("INSERT INTO public.operator_principals(subject,registry_version,capabilities,enabled) VALUES (%s,'ops-v1','{analytics.read,logs.read}',true)", (subject,))
            try:
                c.execute(sql); c.execute(sql)
                assert c.execute("SELECT capabilities FROM public.operator_principals WHERE subject=%s", (subject,)).fetchone()[0].count("credits.allocate") == 1
                with pytest.raises(psycopg.errors.CheckViolation), c.transaction():
                    c.execute("UPDATE public.operator_principals SET capabilities='{users.suspend}' WHERE subject=%s", (subject,))
                c.execute("UPDATE public.operator_principals SET capabilities='{usage.read,payments.read}' WHERE subject=%s", (subject,))   # earlier capabilities stay allowed
            finally:
                c.execute("DELETE FROM public.operator_principals WHERE subject=%s", (subject,))
                c.execute("DELETE FROM public.users WHERE user_id=%s", (subject,))
                c.execute("DELETE FROM auth.users WHERE id=%s", (subject,))


from test_cloud_api import cloud, header  # noqa: E402,F401


def test_the_routes_preview_confirm_grant_and_refuse_others(cloud, world, monkeypatch):
    import delivery_api.app as module
    from delivery_api import auth, config
    client, store, _ = cloud
    _, oid, email = world
    monkeypatch.setattr(config, "DATABASE_URL", APP)
    monkeypatch.setattr(module.app.state,'operator_read_store',reader(),raising=False)
    body = {"email": email, "credits": 25, "reason": "route test"}
    for path in ("/api/admin/operations/grant-preview", "/api/admin/operations/grant"):
        assert client.post(path, json=body).status_code == 401
        assert client.post(path, json=body, headers=header("a")).status_code == 403   # signed in, not an administrator
    module.app.dependency_overrides[auth.require_admin] = lambda: auth.Actor(A.owner_id, "Shy", "shy.alon@gmail.com", True)
    try:
        p = client.post("/api/admin/operations/grant-preview", json=body, headers=header("a"))
        assert p.status_code == 200 and p.json()["after_credits"] == 25
        no = client.post("/api/admin/operations/grant", json=body, headers=header("a"))
        assert no.status_code == 400 and "yes" in no.json()["detail"]   # not typed yes
        assert rows("SELECT count(*) FROM credit_ledger WHERE owner_id=%s", oid) == [(0,)]
        big = client.post("/api/admin/operations/grant", json={**body, "credits": 501, "confirm": "yes"}, headers=header("a"))
        assert big.status_code == 422
        ok = client.post("/api/admin/operations/grant", json={**body, "confirm": "yes"}, headers=header("a"))
        assert ok.status_code == 200 and ok.json()["granted"] is True and ok.json()["after_credits"] == 25
        repeat = client.post("/api/admin/operations/grant", json={**body, "confirm": "yes"}, headers=header("a"))
        assert repeat.json() == {"granted": False, "already_granted_today": True}
        assert any(g["reason"] == "route test" for g in client.get("/api/admin/operations/grants", headers=header("a")).json()["grants"])
        assert client.post("/api/admin/operations/grant", json={**body, "email": "nobody-x@example.test", "credits": 5, "confirm": "yes"}, headers=header("a")).status_code == 400
    finally:
        module.app.dependency_overrides.pop(auth.require_admin, None)
