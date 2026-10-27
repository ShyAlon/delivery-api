"""Content-addressed private operation input, in existing immutable review storage.

No migration, filename or callback chosen by an operation. The existing store
retains with tenant RLS and rejects conflicting immutable records.
"""
import hashlib,json
from .tenancy import current_scope

class ChildOperationRefs:
    def __init__(self,store,task_id):self.store=store;self.task_id=task_id
    def _owner(self):
        task=self.store.task(self.task_id)
        if not task or str(task.get('owner_id'))!=current_scope().owner_id:
            raise PermissionError('operation input owner/task unavailable')
        return current_scope().owner_id
    def put(self,kind,value):
        owner=self._owner()
        payload={'version':1,'owner_id':owner,'task_id':self.task_id,'kind':kind,'value':value}
        ref='child:'+hashlib.sha256(json.dumps(payload,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()
        self.store.retain_decision_review(self.task_id,ref,'child-input-v1',payload)
        return ref
    def get(self,ref,kind):
        owner=self._owner();payload=self.store.decision_review(self.task_id,ref,'child-input-v1')
        if (not payload or payload.get('owner_id')!=owner or payload.get('task_id')!=self.task_id
            or payload.get('version')!=1 or payload.get('kind')!=kind):
            raise PermissionError('durable operation reference unavailable or wrong kind')
        digest='child:'+hashlib.sha256(json.dumps(payload,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()
        if ref!=digest:raise PermissionError('operation input digest changed')
        return payload['value']
