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
    from spec_generator.cooperative_run import cli_stop_binding
    from spec_generator.stop_fence import guarded_publish,check_execution
    from spec_generator.revision_source import read_closed_checkpoint
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
        source=read_closed_checkpoint(root/'spec/checkpoints.sqlite',session_id=cap.session_id,
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
