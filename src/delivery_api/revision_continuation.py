"""Private installed-approved checkpoint preparation, never dispatch.

Original reviewed checkpoint is immutable. Partial private destination remains
for reconciliation and refuses retries. No route imports this module.
"""
import os,json,sqlite3,hashlib
from pathlib import Path
from types import SimpleNamespace
from .tenancy import session_root
from .producer_inputs import _open_directory,_identity


def prepare_private_approved_continuation(store,task_id,*,installation_id,artifacts_base):
    if hasattr(store,'_postgres'):
        from .pg_revision_continuation import prepare
        return prepare(store,task_id,installation_id=installation_id,artifacts_base=artifacts_base)
    from spec_generator.cooperative_run import cli_stop_binding
    from spec_generator.stop_fence import guarded_publish,check_execution
    from .revision_checkpoint_reader import read_source_checkpoint
    from spec_generator.revision_checkpoint import prepare_approved_continuation_checkpoint
    from spec_generator.graph import build_graph,checkpoint_serde
    from spec_generator.stop_publication import FencedCheckpointer,publication_guard
    from langgraph.checkpoint.sqlite import SqliteSaver
    with store._task_operation_guard('task.rerun'):
        basis=store.private_approved_continuation_basis(task_id,installation_id=installation_id,artifacts_base=artifacts_base)
        cap=basis['capability'];root=session_root(Path(artifacts_base),cap.scope,cap.session_id)
        binding=cli_stop_binding()
        if (binding is None or binding[1]!=cap.generation or getattr(binding[0],'_execution_anchor',None)!=cap.execution_anchor
            or Path(binding[0].path)!=root/'interview/stop-request.json'):
            raise ValueError('exact installed writer fence required')
        def authority():
            check_execution();cap.validate(store)
            if store.private_approved_continuation_basis(task_id,installation_id=installation_id,artifacts_base=artifacts_base)!=basis:
                raise ValueError('installed approval basis changed')
        authority()
        marker='revision-continuation:'+installation_id
        if store.decision_review(task_id,marker,basis['review_sha256']):
            raise ValueError('continuation already prepared or ambiguous; explicit reconciliation required')
        source=read_source_checkpoint(store,cap,root/'spec/checkpoints.sqlite',session_id=cap.session_id,
            expected_checkpoint_id=basis['checkpoint_id'],expected_review_phase=basis['phase'])
        store.retain_decision_review(task_id,marker,basis['review_sha256'],{'state':'preparation_pending',
            'checkpoint_id':basis['checkpoint_id'],'approval_reference':basis['approval_reference'],'dispatch_started':False})
    # Pending intent commits before filesystem effects. Never auto-replay it.
    parent=_open_directory(root/'spec');dest=None;conn=None
    try:
        authority();guarded_publish(lambda:os.mkdir('approved-continuation',mode=0o700,dir_fd=parent))
        dest=os.open('approved-continuation',os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=parent);os.fsync(parent)
        def open_checkpoint():
            fd=os.open('checkpoints.sqlite',os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,dir_fd=dest);os.close(fd)
            return sqlite3.connect(f'/proc/self/fd/{dest}/checkpoints.sqlite')
        conn=guarded_publish(open_checkpoint)
        graph=build_graph(SimpleNamespace(record=lambda *a:None),review=True,
            checkpointer=FencedCheckpointer(SqliteSaver(conn,serde=checkpoint_serde()),publication_guard(binding[0],binding[1])))
        receipt=prepare_approved_continuation_checkpoint(installed_state=source,execution_anchor=cap.execution_anchor,
            phase=basis['phase'],artifact_sha256=basis['artifact_sha256'],approval_reference=basis['approval_reference'],
            graph=graph,config={'configurable':{'thread_id':cap.session_id}},validate_authority=authority)
        conn.close();conn=None
        fd=os.open('checkpoints.sqlite',os.O_RDONLY|os.O_NOFOLLOW,dir_fd=dest)
        try:
            os.fsync(fd)
            with os.fdopen(fd,'rb',closefd=False) as stream:digest=hashlib.file_digest(stream,'sha256').hexdigest()
        finally:os.close(fd)
        receipt=receipt|{'source_checkpoint_id':basis['checkpoint_id'],'review_sha256':basis['review_sha256'],'checkpoint_file_sha256':digest}
        raw=json.dumps(receipt,sort_keys=True,indent=2).encode()
        def publish():
            fd=os.open('receipt.json',os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,dir_fd=dest)
            try:
                with os.fdopen(fd,'wb',closefd=False) as stream:stream.write(raw);stream.flush();os.fsync(stream.fileno())
            finally:os.close(fd)
            os.fsync(dest)
        authority();guarded_publish(publish)
        current=_open_directory(root/'spec/approved-continuation')
        try:
            if _identity(current)!=_identity(dest):raise ValueError('continuation namespace changed')
        finally:os.close(current)
        with store._task_operation_guard('task.rerun'):
            authority()
            store.retain_decision_review(task_id,marker+':prepared',basis['review_sha256'],receipt)
        return receipt
    finally:
        if conn is not None:conn.close()
        if dest is not None:os.close(dest)
        os.close(parent)


def reconcile_private_approved_continuation(store,task_id,*,installation_id,artifacts_base,expected_job_id=None):
    """Read-only private receipt/file/DB comparison. Never retries or dispatches."""
    if hasattr(store,'_postgres'):
        from .pg_revision_continuation import reconcile
        return reconcile(store,task_id,installation_id=installation_id,artifacts_base=artifacts_base,expected_job_id=expected_job_id)
    import stat
    from spec_generator.graph import build_graph,checkpoint_serde
    from langgraph.checkpoint.sqlite import SqliteSaver
    with store._task_operation_guard('task.rerun'):
        basis=store.private_approved_continuation_basis(task_id,installation_id=installation_id,artifacts_base=artifacts_base,expected_job_id=expected_job_id)
        marker='revision-continuation:'+installation_id
        pending=store.decision_review(task_id,marker,basis['review_sha256'])
        prepared=store.decision_review(task_id,marker+':prepared',basis['review_sha256'])
        result={'state':'continuation_requires_review','automatic_retry':False,'automatic_cleanup':False,'dispatch_available':False}
        if not pending:return result|{'unverified':['preparation_intent_missing']}
        cap=basis['capability'];root=session_root(Path(artifacts_base),cap.scope,cap.session_id)
        try:
            directory=_open_directory(root/'spec/approved-continuation')
            try:
                def read(name,limit):
                    fd=os.open(name,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=directory)
                    try:
                        info=os.fstat(fd)
                        if not stat.S_ISREG(info.st_mode) or info.st_size>limit:raise ValueError('bounded continuation evidence required')
                        with os.fdopen(fd,'rb',closefd=False) as stream:raw=stream.read(limit+1)
                        if len(raw)>limit:raise ValueError('bounded continuation evidence required')
                        return raw
                    finally:os.close(fd)
                receipt=json.loads(read('receipt.json',2*1024*1024));checkpoint=read('checkpoints.sqlite',64*1024*1024)
                if receipt!=prepared or hashlib.sha256(checkpoint).hexdigest()!=receipt['checkpoint_file_sha256']:
                    raise ValueError('continuation receipt/file/DB differs')
                if (receipt['source_checkpoint_id']!=basis['checkpoint_id'] or receipt['review_sha256']!=basis['review_sha256']
                    or receipt['approval_reference']!=basis['approval_reference'] or receipt['artifact_sha256']!=basis['artifact_sha256']
                    or receipt['execution_session_id']!=cap.session_id or receipt['dispatch_started'] is not False or receipt['provider_invoked'] is not False):
                    raise ValueError('continuation approval evidence differs')
                for name in ('checkpoints.sqlite-wal','checkpoints.sqlite-shm','checkpoints.sqlite-journal'):
                    try:os.stat(name,dir_fd=directory,follow_symlinks=False)
                    except FileNotFoundError:continue
                    raise ValueError('active continuation sidecar')
                fd=os.open('checkpoints.sqlite',os.O_RDONLY|os.O_NOFOLLOW,dir_fd=directory)
                try:
                    conn=sqlite3.connect(f'file:/proc/self/fd/{fd}?mode=ro&immutable=1',uri=True,check_same_thread=False)
                    try:
                        graph=build_graph(SimpleNamespace(record=lambda *a:None),review=True,checkpointer=SqliteSaver(conn,serde=checkpoint_serde()))
                        state=graph.get_state({'configurable':{'thread_id':cap.session_id}})
                        if state.next!=(receipt['next_node'],) or state.config!=receipt['checkpoint']:
                            raise ValueError('continuation checkpoint schedule differs')
                        artifact=state.values[basis['phase']]
                        if hashlib.sha256(json.dumps(artifact,sort_keys=True,separators=(',',':')).encode()).hexdigest()!=basis['artifact_sha256']:
                            raise ValueError('continuation typed artifact differs')
                    finally:conn.close()
                finally:os.close(fd)
                if read('checkpoints.sqlite',64*1024*1024)!=checkpoint or json.loads(read('receipt.json',2*1024*1024))!=receipt:
                    raise ValueError('continuation evidence changed during read')
                current=_open_directory(root/'spec/approved-continuation')
                try:
                    if _identity(current)!=_identity(directory):raise ValueError('continuation namespace changed')
                finally:os.close(current)
            finally:os.close(directory)
        except (OSError,ValueError,KeyError,TypeError):return result|{'unverified':['continuation_file_or_receipt_unverified']}
        return result|{'state':'continuation_prepared_verified_no_dispatch','unverified':[],'next_node':receipt['next_node']}
