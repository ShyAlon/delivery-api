"""Original-job PG runtime for an admitted generator subprocess.

The parent passes a bounded binding through a private inherited descriptor.
Credentials never appear in argv or in an added environment variable.
"""
import json
import os
import socket
import sys
from contextlib import contextmanager


@contextmanager
def launch_context(store, capability, gateway, command, env):
    from .generator_documents import GeneratorDocuments, prepare_document_workspace
    from .same_host_producer import ProducerRecoveryPaused

    if len(command) < 3 or command[1:3] != ['-m', 'spec_generator.cli']:
        raise ValueError('fixed generator module required')
    job = store.worker_job(gateway.job_id)
    docs = GeneratorDocuments(store, capability, gateway.job_id, expected_attempt=job['attempt'])
    if store.producer_result(gateway.job_id) is not None or store.producer_paid_evidence(gateway.job_id):
        raise ProducerRecoveryPaused('process result or provider evidence exists; reconcile publication before any rerun')
    prepare_document_workspace(store, capability.task_id, docs, current_phase=job['kind'].split(':')[-1])
    payload = {
        'version': 1, 'dsn': store._postgres.dsn,
        'owner_id': capability.scope.owner_id, 'auth_session_id': capability.scope.session_id,
        'task_id': capability.task_id, 'job_id': gateway.job_id, 'attempt': job['attempt'],
        'session_id': capability.session_id, 'generation': capability.generation,
    }
    raw = json.dumps(payload, separators=(',', ':')).encode() + b'\n'
    if len(raw) > 32768:
        raise ValueError('bounded PG launch binding required')
    parent, child = socket.socketpair()
    try:
        parent.sendall(raw)
        parent.shutdown(socket.SHUT_WR)
        wrapped = [sys.executable, '-m', 'delivery_api.pg_generator_process', *command[3:]]
        yield wrapped, {**(env or {}), 'REQUI_PG_GENERATOR_FD': str(child.fileno())}, child.fileno(), docs
    finally:
        parent.close()
        child.close()


def capture_binding(store, cfg):
    from .worker_capability import WorkerCapability
    from .generator_documents import GeneratorDocuments

    cap = WorkerCapability.capture(store, cfg['task_id'])
    if cap.session_id != cfg['session_id'] or cap.generation != cfg['generation']:
        raise PermissionError('PG execution binding changed')
    docs = GeneratorDocuments(store, cap, cfg['job_id'], expected_attempt=cfg['attempt'])
    with docs.saver._cursor():
        pass
    return cap, docs


def main():
    with socket.socket(fileno=int(os.environ['REQUI_PG_GENERATOR_FD'])) as channel:
        with channel.makefile('rb') as stream:
            raw = stream.readline(32769)
    if len(raw) > 32768 or not raw.endswith(b'\n'):
        raise ValueError('bounded PG launch binding required')
    cfg = json.loads(raw)
    if cfg.pop('version') != 1:
        raise ValueError('PG launch version required')

    from pathlib import Path
    from .postgres_store import PostgresStore
    from .tenancy import TenantScope, tenant_context
    from .generator_checkpoint import GeneratorCheckpoint
    from .pg_execution_fence import PostgresExecutionFence
    from .inprocess_engine import stage_models
    from spec_generator.worker_runtime import JobRuntime, job_runtime
    from spec_generator.cooperative_run import cli_execution
    from spec_generator.telemetry import UsageLog
    from spec_generator import cli
    from . import db_pool

    # Parent 10 + two admitted children 2 each fits below the 15-connection cap.
    db_pool.PROCESS_POOL_MAX = 2
    store = PostgresStore(cfg['dsn'])
    scope = TenantScope(cfg['owner_id'], cfg['auth_session_id'])
    with tenant_context(scope):
        cap, docs = capture_binding(store, cfg)
        # The orchestrator passes the owner namespace, not the shared root.
        root = Path(os.environ['VMODEL_ARTIFACTS_DIR']) / cap.session_id
        runtime = JobRuntime(stage_models(os.environ), str(root / 'private-llm-calls.jsonl'), lambda text: None)
        runtime.bind_worker(owner_id=scope.owner_id, task_id=cap.task_id, job_id=cfg['job_id'],
                            generation=cap.generation, session_root=root)
        runtime.documents = docs

        runtime.proposal_sink = docs.retain_proposal
        runtime.checkpoint_factory = lambda namespace: GeneratorCheckpoint(
            store, cap, cfg['job_id'], namespace, expected_attempt=cfg['attempt'])
        fence = PostgresExecutionFence(store, cap, root=root)
        with job_runtime(runtime), cli_execution(fence, UsageLog(cap.session_id)):
            cli.main()


if __name__ == '__main__':
    main()
