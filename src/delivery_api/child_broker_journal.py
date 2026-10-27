"""Parent-owned raw-response journal over the existing immutable staging table.

No migration/DDL. Every operation uses delivery's role + owner/Auth claims.
No child has write access through this object. An insert race fails closed;
a duplicate incomplete row is NOT permission to reserve or dispatch again.
"""
import json
import time
from .tenancy import current_scope
from .child_provider_broker import BrokerPaused


class ChildBrokerJournal:
    def __init__(self,store,operation):self.store=store;self.operation=operation

    def _identity(self,key):
        operation_id,call_id,stage=key
        b=self.operation.binding;scope=current_scope()
        if (scope.owner_id,scope.session_id)!=(str(b.owner_id),str(b.auth_session_id)) or operation_id!=self.operation.operation_id:
            raise PermissionError('journal original owner/Auth-session binding required')
        # Existing staging namespace is separate from graph execution points.
        return (str(b.owner_id),b.session_id,b.generation,'child:'+operation_id,call_id,stage,'',0)

    def load(self,key):
        identity=self._identity(key)
        with self.store._postgres.transaction() as conn:
            row=conn.execute('SELECT request_sha256,provider,model,attempt_id,response,usage '
                'FROM staged_provider_responses WHERE owner_id=%s::uuid AND session_id=%s AND generation=%s '
                'AND execution_point=%s AND node=%s AND stage=%s AND item=%s AND ordinal=%s',identity).fetchone()
            return dict(row) if row else None

    def begin(self,key,digest,provider,model):
        identity=self._identity(key)
        with self.store._postgres.transaction() as conn:
            conn.execute('INSERT INTO staged_provider_responses(owner_id,session_id,generation,execution_point,node,stage,item,ordinal,'
                'request_sha256,provider,model,dispatched_at) VALUES (%s::uuid,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)',
                (*identity,digest,provider,model,time.time()))

    def bind(self,key,attempt):
        identity=self._identity(key)
        with self.store._postgres.transaction() as conn:
            row=conn.execute('UPDATE staged_provider_responses SET attempt_id=%s WHERE owner_id=%s::uuid AND session_id=%s AND generation=%s '
                'AND execution_point=%s AND node=%s AND stage=%s AND item=%s AND ordinal=%s AND attempt_id IS NULL RETURNING ordinal',
                (attempt,*identity)).fetchone()
            if row is None:raise BrokerPaused('durable attempt binding missing or repeated')

    def complete(self,key,response,usage):
        identity=self._identity(key)
        with self.store._postgres.transaction() as conn:
            row=conn.execute('UPDATE staged_provider_responses SET response=%s::jsonb,usage=%s::jsonb,completed_at=%s '
                'WHERE owner_id=%s::uuid AND session_id=%s AND generation=%s AND execution_point=%s AND node=%s '
                'AND stage=%s AND item=%s AND ordinal=%s AND response IS NULL RETURNING ordinal',
                (json.dumps(response),json.dumps(usage),time.time(),*identity)).fetchone()
            if row is None:raise BrokerPaused('raw response completion missing or repeated')
