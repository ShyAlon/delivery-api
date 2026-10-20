"""Operator credits and spend view against the real Postgres: the sums, the read-only role, and the route."""
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
SCALE = 10**9
USD = 10**12


@pytest.fixture
def world(store):
    """Account X: 100 credits, a 5-credit debit now, a 3-credit debit 3 days ago; one provider charge of $0.50 now and $1.00 3 days ago."""
    oid = str(uuid.uuid4())
    now = time.time()
    with psycopg.connect(DSN, autocommit=True) as c:
        c.execute("INSERT INTO auth.users(id,email,email_confirmed_at) VALUES(%s,%s,now())", (oid, oid + "@example.test"))
        c.execute("INSERT INTO public.users(user_id,label,created_at,updated_at) VALUES(%s,%s,0,0) ON CONFLICT (user_id) DO UPDATE SET label=EXCLUDED.label", (oid, "spend-" + oid[:6]))
        c.execute("INSERT INTO credit_accounts(owner_id,balance_units,units_per_credit,updated_at) VALUES(%s,%s,%s,0)", (oid, 100 * SCALE, SCALE))
        for kind_delta, age in ((5, 0), (3, 3 * 86400)):
            c.execute("INSERT INTO public.credit_ledger(owner_id,kind,delta_units,balance_before_units,balance_after_units,units_per_credit,before_units_per_credit,actor,db_role,txid,recorded_at) "
                      "VALUES(%s,'debit',%s,%s,%s,%s,%s,'t','t',1,%s)", (oid, -kind_delta * SCALE, 100 * SCALE, (100 - kind_delta) * SCALE, SCALE, SCALE, now - age))
        c.execute("UPDATE public.operator_principals SET enabled=false")
        c.execute("INSERT INTO public.operator_principals(subject,registry_version,capabilities,enabled) VALUES (%s,'ops-v1',%s,true) "
                  "ON CONFLICT (subject) DO UPDATE SET enabled=true,registry_version='ops-v1',capabilities=EXCLUDED.capabilities",
                  (A.owner_id, ["analytics.read", "sessions.read", "debug.read", "logs.read"]))
    yield store, oid, "spend-" + oid[:6], now
    with psycopg.connect(DSN, autocommit=True) as c:
        c.execute("DELETE FROM public.operator_principals WHERE subject=%s", (A.owner_id,))   # leave no operator row behind for other tests
        c.execute("DELETE FROM credit_accounts WHERE owner_id=%s", (oid,))


def read(store, owner=A, caps=frozenset({"analytics.read"}), version="ops-v1"):
    reader = OperatorReadStore(APP, OperatorPrincipal(owner.owner_id, version, caps))
    with tenant_context(owner):
        return reader.spend(store)


def test_the_operator_sees_balance_and_credits_used_per_account(world):
    store, oid, label, _ = world
    row = next(r for r in read(store)["accounts"] if r["account"] == label)
    assert row["balance_credits"] == 100.0 and row["credits_24h"] == 5.0 and row["credits_7d"] == 8.0
    assert set(row) == {"account", "balance_credits", "credits_24h", "credits_7d", "expense_24h_usd", "expense_7d_usd"}   # no ids


def test_the_operator_role_cannot_write_and_non_operators_are_denied(world):
    store, oid, label, _ = world
    with pytest.raises(TenantAccessDenied):
        read(store, owner=B)
    with pytest.raises(TenantAccessDenied):
        read(store, caps=frozenset({"logs.read"}))
    with pytest.raises(Exception):
        with psycopg.connect(APP) as c:
            c.execute("SET LOCAL ROLE requi_operator_read")
            c.execute("UPDATE public.credit_accounts SET balance_units=0")


def test_vendor_expense_is_summed_over_24_hours_and_7_days_in_dollars(world):
    store, oid, label, now = world
    with tenant_context(B):
        task = store.create_task("spend-" + uuid.uuid4().hex)
        store.set_engine_session(task["task_id"], "s-" + uuid.uuid4().hex)
        job = store.enqueue_worker(task["task_id"], "phase:needs")
        store.claim_worker(job)
    with psycopg.connect(DSN, autocommit=True) as c:
        made_account = c.execute("INSERT INTO credit_accounts(owner_id,balance_units,units_per_credit,updated_at) VALUES(%s,%s,%s,0) ON CONFLICT (owner_id) DO NOTHING RETURNING 1", (B.owner_id, SCALE, SCALE)).fetchone() is not None
        for name, usd, age in (("n", 5 * 10**11, 0), ("o", 10**12, 3 * 86400), ("x", 7 * 10**12, 30 * 86400)):
            c.execute("INSERT INTO provider_attempts(attempt_id,owner_id,job_id,config_version,quote,fingerprint,state,expense_reserved,retail_reserved,expense_actual,retail_actual,usage,created_at,updated_at,settled_at) "
                      "VALUES (%s,%s,%s,'test-only','{}','test-only','settled',0,0,%s,0,'{}',%s,%s,%s)", ("sp-" + uuid.uuid4().hex, B.owner_id, job, usd, now - age, now - age, now - age))
    try:
        row = next(r for r in read(store)["accounts"] if r["expense_24h_usd"] == 0.5)
        assert row["expense_7d_usd"] == 1.5   # the 30-day-old charge is outside both windows
    finally:
        with psycopg.connect(DSN, autocommit=True) as c:
            c.execute("DELETE FROM provider_attempts WHERE attempt_id LIKE 'sp-%'")
            if made_account:
                c.execute("DELETE FROM credit_accounts WHERE owner_id=%s", (B.owner_id,))


from test_cloud_api import cloud, header  # noqa: E402,F401


def test_the_spend_route_serves_the_operator_refuses_others_and_says_what_to_do_when_not_switched_on(cloud, world, monkeypatch):
    import delivery_api.app as module
    from delivery_api import auth, config
    client, store, _ = cloud
    path = "/api/admin/operations/spend"
    monkeypatch.setattr(config, "DATABASE_URL", APP)
    module._stop_readers.clear()
    assert client.get(path).status_code == 401
    assert client.get(path, headers=header("a")).status_code == 403
    module.app.dependency_overrides[auth.require_admin] = lambda: auth.Actor(A.owner_id, "Shy", "shy.alon@gmail.com", True)
    try:
        ok = client.get(path, headers=header("a"))
        assert ok.status_code == 200 and any(r["credits_24h"] == 5.0 for r in ok.json()["accounts"])
        with psycopg.connect(DSN) as c:
            c.execute("UPDATE public.operator_principals SET enabled=false")
        off = client.get(path, headers=header("a"))
        assert off.status_code == 503 and "not switched on" in off.json()["detail"]["error"]
    finally:
        module.app.dependency_overrides.pop(auth.require_admin, None)
        module._stop_readers.clear()
