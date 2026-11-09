"""Real graph/child and zero-cost production admission, fixture HTTP only."""
import json,sys,uuid
import pytest
from pathlib import Path
from delivery_api.tenancy import tenant_context
from test_postgres_store import A,DSN,store


@pytest.mark.parametrize('active_stop',[False,True])
def test_image_fixture_real_child_with_zero_balance_production_gateway(store,tmp_path,active_stop):
    import importlib.util,psycopg
    from delivery_api.production_gateway import ProductionGateways,PRICES_ENV,CEILING_ENV
    from delivery_api.child_interview import ChildInterviewClient
    from vmodel_contracts.interactive import StartSessionRequest
    spec=importlib.util.spec_from_file_location('zero_fixture',Path(__file__).parents[1]/'scripts/docker/fixture_services.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    services=module.Services(tmp_path/'keys');_,port=services.start(directory=tmp_path/'keys')
    row={'provider':'ollama','model':module.MODEL,'price_version':'image-fixture-v1','source':'owned zero-cost fixture',
         'expense_input':0,'expense_output':0,'retail_input':0,'retail_output':0,'retail_units_per_credit':10**9}
    ceiling={'model':module.MODEL,'context_tokens':32768,'maximum_output_tokens':12288,
             'source':'owned zero-cost fixture','bound_version':'image-fixture-v1','protective_run_ceiling':False}
    with psycopg.connect(DSN) as connection:
        connection.execute('INSERT INTO credit_accounts(owner_id,balance_units,units_per_credit,updated_at) VALUES(%s,0,1000000000,0)',(A.owner_id,))
        connection.execute('INSERT INTO admission_limits VALUES(%s,%s,86400,NULL,NULL,NULL,NULL,60,NULL,3)',(A.owner_id,'image-fixture'))
    factory=ProductionGateways(store,{PRICES_ENV:json.dumps([row]),CEILING_ENV:json.dumps(ceiling)})
    class PinnedFactory:
        models={'ollama':module.MODEL}
        def for_task(self,tid):return factory
        def __call__(self,cap,job):return factory(cap,job)
    pinned=PinnedFactory()
    command=[sys.executable,'-c',"import sys,runpy;sys.path[:0]=sys.argv[1:];runpy.run_module('delivery_api.child_entry',run_name='__main__')",str(Path(__file__).parents[2]/'interview-engine/src'),str(Path(__file__).parents[2]/'spec-generator/src')]
    try:
        with tenant_context(A):
            tid=store.create_task('real image zero child '+uuid.uuid4().hex)['task_id'];sid=str(uuid.uuid4());store.set_engine_session(tid,sid)
            client=ChildInterviewClient(store,tmp_path/'artifacts',gateway_factory=pinned,dsn=DSN.replace('user=sandbox','user=delivery_app'),endpoint=f'http://127.0.0.1:{port}/v1/chat/completions',command=command)
            if active_stop:
                from concurrent.futures import ThreadPoolExecutor
                from delivery_api.cooperative_stop import request_stop
                def start_held():
                    with tenant_context(A):
                        try:client.start(StartSessionRequest(task_id=tid,session_id=sid,idea='EXACT_IMAGE_STOP: A booking app',model_route='ollama'))
                        except RuntimeError:return
                        raise AssertionError('stopped child returned success')
                with ThreadPoolExecutor(1) as workers:
                    future=workers.submit(start_held)
                    try:
                        assert services.active.wait(10),'real child never reached held provider'
                        assert client.supervisor.active and not future.done()
                        pending=request_stop(store,client,tid)
                        assert pending['state']=='stop_requested' and pending['delete_allowed'] is False
                    finally:services.release.set()
                    future.result(timeout=15)
                assert client.supervisor.last_result.reaped
                # HTTP Start's late exception callback writes a failed phase.
                # It must not undo the owner Stop task state.
                store.update_phase(tid,'intent','failed',detail='stopped child')
                assert store.task(tid)['state']=='stop_requested'
                assert len(services.calls)==1 and not services.active.is_set()
                stopped=request_stop(store,client,tid,reconcile=True)
                assert stopped['state']=='stopped' and stopped['delete_allowed'] is True,stopped
                store.update_phase(tid,'intent','running',detail='late completion')
                assert store.task(tid)['state']=='stopped'
                return
            result=client.start(StartSessionRequest(task_id=tid,session_id=sid,idea='A test booking app',model_route='ollama'))
            assert result.snapshot.open_input is not None
            assert services.calls and client.supervisor.last_result.reaped
            assert all(call['model']==module.MODEL for call in services.calls)
            from vmodel_contracts.interactive import InputResponse
            answered=client.submit(sid,InputResponse(input_id=result.snapshot.open_input.input_id,value='Customers book a time slot',actor_id='fixture',expected_sequence=result.snapshot.sequence))
            assert answered.snapshot.sequence>result.snapshot.sequence
            # The verified answer parks its warm child alive; the stop below
            # releases it through the durable fence, not a reap.
            parked=client._warm[sid]['handle']
            assert parked.process.returncode is None
            from delivery_api.cooperative_stop import request_stop
            stopped=request_stop(store,client,tid)
            assert stopped['state']=='stopped' and stopped['delete_allowed'] is True,stopped
            # Completion cannot be inferred from lease expiry or job state.
            with store._postgres.transaction() as conn:
                conn.execute("UPDATE engine_sessions SET control=control-'stop_quiescent' WHERE session_id=%s",(sid,))
                conn.execute("DELETE FROM decision_reviews WHERE task_id=%s AND revision='child-completion-v1'",(tid,))
            assert store.confirm_child_session_quiescent(sid) is False
    finally:
        services.close();factory.close()
        with psycopg.connect(DSN) as connection:
            for table in ('provider_attempts','credit_accounts','admission_limits'):
                connection.execute('DELETE FROM '+table+' WHERE owner_id=%s',(A.owner_id,))
