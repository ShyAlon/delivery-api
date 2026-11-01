"""Actual PG uniqueness and retained hidden evidence, no live DB."""
import uuid
import pytest
from delivery_api.tenancy import tenant_context
from test_postgres_store import A,B,DSN
import psycopg


def test_hidden_name_reusable_but_visible_duplicate_and_rename_refused(store):
    name='Reuse '+uuid.uuid4().hex
    with tenant_context(A):
        old=store.create_task(name)
        store.delete_task(old['task_id'])
        new=store.create_task(name.upper())
        assert new['task_id']!=old['task_id']
        assert store.task(old['task_id']) is None
        with psycopg.connect(DSN) as conn:
            assert conn.execute('SELECT deleted_at FROM tasks WHERE task_id=%s',(old['task_id'],)).fetchone()[0] is not None
        with pytest.raises(ValueError,match='already exists'):store.create_task(name)
        other=store.create_task('Other '+uuid.uuid4().hex)
        with pytest.raises(ValueError,match='already exists'):store.rename_task(other['task_id'],name)
    with tenant_context(B):assert store.create_task(name)['task_id']


def test_old_full_unique_index_is_pending_until_replaced(store):
    import importlib.util
    from pathlib import Path
    root=Path(__file__).parents[1]
    spec=importlib.util.spec_from_file_location('name_readiness',root/'scripts/ops/schema-readiness.py')
    m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
    with psycopg.connect(DSN) as conn:
        assert m.object_exists(conn,'visible-title-index:idx_tasks_tenant_title')
        conn.execute('DROP INDEX public.idx_tasks_tenant_title')
        # Other tests may already have valid hidden/visible duplicates. Make
        # fixture titles distinct only in this rollback-only transaction.
        conn.execute("UPDATE tasks SET title=title||task_id")
        conn.execute('CREATE UNIQUE INDEX idx_tasks_tenant_title ON public.tasks(owner_id,lower(title))')
        assert not m.object_exists(conn,'visible-title-index:idx_tasks_tenant_title')
        conn.rollback()
