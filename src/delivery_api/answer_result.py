"""Bounded private child result; only emitted after its final publish returned."""
import base64
import hashlib
import json

MAX_BYTES=1024*1024
CHUNK=1800


def result_frames(operation,snapshot,accepted_event,token):
    raw=json.dumps({'snapshot':snapshot.model_dump(mode='json'),'accepted_event':accepted_event.model_dump(mode='json')},separators=(',',':')).encode()
    if len(raw)>MAX_BYTES:return []  # Parent uses the ordinary durable read.
    data=base64.b64encode(raw).decode();digest=hashlib.sha256(raw).hexdigest()
    count=(len(data)+CHUNK-1)//CHUNK
    return [{'operation_id':operation.operation_id,'token':token,'index':i,'count':count,'digest':digest,'data':data[i*CHUNK:(i+1)*CHUNK]} for i in range(count)]


class AnswerResult:
    def __init__(self,operation,token=None):
        import secrets
        self.token=token or secrets.token_hex(32)
        self.operation=operation;self.parts=[];self.identity=None;self.snapshot=None;self.accepted_event=None
    def accept(self,row):
        from vmodel_contracts.interactive import EngineSnapshot,InteractiveEvent,EventType
        if set(row)!={'operation_id','token','index','count','digest','data'} or row['operation_id']!=self.operation.operation_id or row['token']!=self.token:return
        index,count=row['index'],row['count']
        if type(index) is not int or type(count) is not int or not 1<=count<=800:return
        if not isinstance(row['data'],str) or len(row['data'])>CHUNK:return
        if not isinstance(row['digest'],str) or len(row['digest'])!=64:return
        identity=(count,row['digest'])
        if self.identity is None:self.identity=identity
        if self.identity!=identity or index!=len(self.parts):return
        self.parts.append(row['data'])
        if len(self.parts)!=count:return
        raw=base64.b64decode(''.join(self.parts),validate=True)
        if len(raw)>MAX_BYTES or hashlib.sha256(raw).hexdigest()!=row['digest']:return
        payload=json.loads(raw)
        snapshot=EngineSnapshot.model_validate(payload['snapshot'])
        accepted=InteractiveEvent.model_validate(payload['accepted_event'])
        binding=self.operation.binding;body=self.operation.operation
        if (str(snapshot.session_id)!=binding.session_id or snapshot.task_id!=binding.task_id
            or snapshot.sequence<=body.expected_sequence):return
        if (accepted.session_id!=snapshot.session_id or accepted.task_id!=snapshot.task_id or accepted.sequence!=snapshot.sequence
            or accepted.event_type!=EventType.INPUT_ACCEPTED or accepted.process_status!=snapshot.process_status or accepted.next_step!=snapshot.next_step):return
        self.snapshot=snapshot;self.accepted_event=accepted
