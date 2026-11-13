"""Real owned PG documents and the existing authenticated debug endpoint."""
import io,json,os,zipfile
from fastapi.testclient import TestClient
from delivery_api.tenancy import tenant_context
from test_account_debug_capability import setup,clean_debug_registry


def test_retained_rejections_and_exact_source_export_only_for_owner(pg_workspace_factory,pg_task_app,monkeypatch):
    import delivery_api.app as module
    from delivery_api.generator_documents import GeneratorDocuments
    from delivery_api.tenant_files import authorized_session
    from vmodel_contracts.runtime import artifacts_root
    with pg_workspace_factory() as owner,pg_workspace_factory() as other:
        tid=setup(owner,monkeypatch,module)
        with tenant_context(owner.scope):
            store=owner.store
            job=store.enqueue_worker(tid,'phase:architecture');cap=store.claim_worker(job)
            docs=GeneratorDocuments(store,cap,job)
            root=authorized_session(artifacts_root(os.environ.get('VMODEL_ARTIFACTS_DIR')),store.engine_session(tid),store)
            # Exercise the real generator capture seam, with no model call.
            from spec_generator.worker_runtime import JobRuntime,job_runtime
            from spec_generator.model_config import StageModel,capture_rejected_completion
            runtime=JobRuntime({'architecture':StageModel('ollama','fake')},str(root/'private-llm-calls.jsonl'),lambda text:None)
            runtime.bind_worker(owner_id=owner.scope.owner_id,task_id=tid,job_id=job,generation=cap.generation,session_root=root)
            runtime.documents=docs
            with job_runtime(runtime):
                path=capture_rejected_completion('architecture','ADR-1',2,
                    '{"rationale":"Actual rejected wording", "password":"never-export"}','Final citation rejection')
            from pathlib import Path
            key=Path(path).relative_to(root).as_posix()
            docs.write('interview/intent_model.json',b'{"founder_register":{"items":[{"decision":"Current source decision"}]}}')
            store.finish_worker(job)
        client=TestClient(pg_task_app(owner),headers=owner.headers)
        response=client.post(f'/api/tasks/{tid}/debug.zip',json={})
        assert response.status_code==200,response.text
        with zipfile.ZipFile(io.BytesIO(response.content)) as z:
            assert b'Actual rejected wording' in z.read(key)
            assert b'never-export' not in z.read(key)
            assert b'Final citation rejection' in z.read('rejected-llm-outputs/manifest.jsonl')
            assert b'Current source decision' in z.read('interview/intent_model.json')
        # An authenticated different owner cannot address the original task.
        foreign=TestClient(pg_task_app(other),headers=other.headers)
        assert foreign.post(f'/api/tasks/{tid}/debug.zip',json={}).status_code==404
