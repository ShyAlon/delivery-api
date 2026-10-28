"""Worker query consumers must not depend on SQLite SQL translation."""
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4
import pytest
from delivery_api.tenancy import TenantScope,TenantAccessDenied,tenant_context


def test_native_worker_claim_resume_stop_and_owner_fences(pg_workspace_factory,monkeypatch, pg_bridge_poison):
    _Connection = pg_bridge_poison
    with pg_workspace_factory() as a,pg_workspace_factory() as b:
        s=a.store
        with tenant_context(a.scope):
            tid=s.create_task('Native worker')['task_id'];s.set_engine_session(tid,'native-worker-'+uuid4().hex)
        original=_Connection.execute
        def reject_worker(self,sql,parameters=()):
            if 'worker_jobs' in sql and not sql.startswith('\nSELECT t.*'):
                raise AssertionError('worker operation still consumes query bridge')
            return original(self,sql,parameters)
        with monkeypatch.context() as patch:
            patch.setattr(_Connection,'execute',reject_worker)
            with tenant_context(a.scope):
                job=s.enqueue_worker(tid,'fake-native')
                assert s.worker_job(job)['state']=='pending'
                assert job in [j['job_id'] for j in s.recoverable_worker_jobs()]
                assert s.task_worker_state(tid)['worker_jobs'][0]['job_id']==job
                assert s.task_worker_state(tid)['provider_attempts']==[]
            def claim(_):
                with tenant_context(a.scope):return s.claim_worker(job) is not None
            with ThreadPoolExecutor(4) as pool:assert sum(pool.map(claim,range(4)))==1
            with tenant_context(b.scope):
                assert s.worker_job(job) is None
                assert s.claim_worker(job) is None
                assert s.task_worker_state(tid)=={'worker_jobs':[],'provider_attempts':[]}
                with pytest.raises(KeyError):s.reauthorize_worker(job)
            with tenant_context(a.scope):
                s.pause_interrupted_worker(job);assert s.claim_worker(job)
                s.finish_worker(job,failed=True)
            new_scope=TenantScope(a.scope.owner_id,str(uuid4()))
            with a.admin() as admin:admin.execute('INSERT INTO auth.sessions(id,user_id) VALUES (%s,%s)',(new_scope.session_id,new_scope.owner_id))
            with tenant_context(new_scope):
                with pytest.raises(TenantAccessDenied):s.claim_worker(job)
                s.reauthorize_worker(job);assert s.claim_worker(job).scope.session_id==new_scope.session_id
                s.finish_worker(job);s.finish_worker(job)
                assert s.worker_job(job)['attempt']==3
                assert s.worker_job(job)['state']=='done'
                assert s.claim_worker(job) is None
                stopjob=s.enqueue_worker(tid,'fake-stop');assert s.claim_worker(stopjob)
            # Stop request itself belongs to the still-unported lifecycle theme.
            with tenant_context(new_scope):
                with monkeypatch.context() as p:
                    p.setattr(_Connection,'execute',original)
                    s.request_task_stop(tid)
                from delivery_api.attempt_ledger import AdmissionDenied
                with pytest.raises(AdmissionDenied):s.finish_stopped_worker(stopjob,worker_quiescent=False)
                s.finish_stopped_worker(stopjob,worker_quiescent=True)
                assert s.worker_job(stopjob)['state']=='done'


def test_worker_enqueue_failure_rolls_back_native_write(pg_workspace_factory,monkeypatch):
    with pg_workspace_factory() as w,tenant_context(w.scope):
        s=w.store;tid=s.create_task('Rollback worker')['task_id'];s.set_engine_session(tid,'rollback-worker-'+uuid4().hex)
        execute=s._execute_native
        def fail_after_insert(sql,parameters=(),*,write=False):
            result=execute(sql,parameters,write=write)
            if sql.startswith('INSERT INTO worker_jobs'):raise RuntimeError('injected after worker write')
            return result
        with monkeypatch.context() as patch:
            patch.setattr(s,'_execute_native',fail_after_insert)
            with pytest.raises(RuntimeError,match='injected after worker write'):s.enqueue_worker(tid,'fake')
        assert s.task_worker_state(tid)['worker_jobs']==[]
        assert s.recoverable_worker_jobs()==[]
