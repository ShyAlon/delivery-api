"""Owner-scoped Stop coordination, without cancelling accepted work.

Engine quiescence does not reconcile provider uncertainty or running delivery
jobs. Those must finish through their own verified completion paths.
"""
from vmodel_contracts.interactive import ProcessStatus,NextStep
from .attempt_ledger import AdmissionDenied


def request_stop(store,engine,task_id,*,reconcile=False):
    task=store.task(task_id)
    if not task:raise KeyError(task_id)
    session=store.engine_session(task_id)
    if not session:raise AdmissionDenied('bound engine session required for cooperative Stop')
    binding=store.session_binding(session)
    if not binding or binding['task_id']!=task_id or str(binding['owner_id'])!=task['owner_id']:
        raise AdmissionDenied('owned session binding required for Stop')
    # Commit before network: if the engine is unreachable, no new cloud call is
    # admitted. A repeated request is idempotent and can deliver the marker later.
    task=store.request_task_stop(task_id)
    snapshot=engine.reconcile_stop(session) if reconcile else engine.stop(session)
    if snapshot.session_id!=session or snapshot.task_id!=task_id or snapshot.next_step!=NextStep.STOP or snapshot.process_status not in (ProcessStatus.STOP_REQUESTED,ProcessStatus.STOPPED):
        raise AdmissionDenied('engine Stop response binding/state mismatch')
    pending_reason=None
    if snapshot.process_status==ProcessStatus.STOPPED:
        try:task=store.confirm_task_stopped(task_id,engine_quiescent=True)
        except AdmissionDenied:
            pending_reason='Worker completion or provider usage is not reconciled.'
    return {'task_id':task_id,'state':task['state'],'engine_status':int(snapshot.process_status),
            'delete_allowed':task['state']=='stopped' and task.get('stopped_at') is not None,
            'pending_reason':pending_reason,'accepted_calls_may_bill':True}
