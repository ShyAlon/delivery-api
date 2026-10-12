import sys
from pathlib import Path
from types import SimpleNamespace
import pytest
sys.path.insert(0,str(Path(__file__).parents[1]/'scripts/item6'))
from pipeline_executor import PrivatePipeline,require_approved_srs


def test_canonical_store_phase_key_rows_are_accepted():
    require_approved_srs({'phases':[{'phase_key':'intent','state':'done'},{'phase_key':'srs','state':'approved'}]})


@pytest.mark.parametrize('phases',[
    [{'key':'srs','state':'approved'}],
    [{'phase_key':'srs','state':'done'}],
    [{'phase_key':'needs','state':'done'}],
    [{'phase_key':'srs','state':'approved'},{'phase_key':'srs','state':'approved'}],
    None,
])
def test_bad_phase_shapes_stop_before_enqueue_or_compute(phases):
    class Store:
        def enqueue_worker(self,*args,**kw):raise AssertionError('must not enqueue')
    runner=PrivatePipeline(store=Store(),artifacts_base=Path('/tmp'),model='fixture',gateway_factory=lambda *a:None,
        generator_clients=lambda *a:None,interview_clients=lambda *a:None,answer_input=lambda *a:None,
        review_gate=lambda *a:None,progress=lambda *a:None)
    from delivery_api.tenancy import TenantScope
    with pytest.raises(ValueError):
        runner.execute_generator_existing(SimpleNamespace(root=Path('/tmp')),TenantScope('00000000-0000-0000-0000-000000000001','00000000-0000-0000-0000-000000000002'),
            {'task_id':'fixture','phases':phases},prior_job='fixture-job',generator_resume={})


def test_real_postgres_task_shape_keeps_canonical_key():
    import os,uuid
    dsn=os.getenv('DELIVERY_TEST_POSTGRES_DSN')
    if not dsn:pytest.skip('isolated PG required')
    from delivery_api.postgres_store import PostgresStore
    from delivery_api.tenancy import TenantScope,tenant_context
    store=PostgresStore(dsn.replace('user=sandbox','user=delivery_app'))
    scope=TenantScope('00000000-0000-0000-0000-000000000001','00000000-0000-0000-0000-000000000001')
    with tenant_context(scope):
        task=store.create_task('phase-shape-'+uuid.uuid4().hex)
        try:
            store.update_phase(task['task_id'],'srs','approved')
            actual=store.task(task['task_id'])
            assert all('phase_key' in p and 'key' not in p for p in actual['phases'])
            require_approved_srs(actual)
        finally:store.delete_task(task['task_id'])
