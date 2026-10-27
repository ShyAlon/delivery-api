"""Inert child-operation contract. No process, network, file or DB effects."""
import json
import pytest
from pydantic import ValidationError
from delivery_api.child_execution_contract import decode_operation,encode_operation,require_binding,MAX_FRAME_BYTES


def operation():
    return {'version':1,'operation_id':'op-1','binding':{
        'owner_id':'00000000-0000-0000-0000-000000000001',
        'auth_session_id':'00000000-0000-0000-0000-000000000002',
        'task_id':'task-1','session_id':'session-1','job_id':'job-1',
        'generation':1,'lease_epoch':2,'provider':'deepseek','model':'deepseek-flash'},
        'operation':{'kind':'interview.answer','wait_id':'input-1','expected_sequence':3,
                     'review_revision':'engine:s:3','answer_ref':'answer-1','checkpoint_sha256':'a'*64}}


def frame(value):return json.dumps(value).encode()+b'\n'


def test_roundtrip_is_versioned_typed_and_frozen():
    message=decode_operation(frame(operation()))
    assert message.operation.kind=='interview.answer'
    assert decode_operation(encode_operation(message))==message
    with pytest.raises(ValidationError):message.version=2
    with pytest.raises(ValidationError):message.binding.lease_epoch=3
    with pytest.raises(ValidationError):message.operation.answer_ref='other'


@pytest.mark.parametrize('change',[
    lambda x:x.update(version=2),lambda x:x.update(command=['sh','-c','anything']),
    lambda x:x['binding'].update(owner_id='not-a-uuid'),
    lambda x:x['binding'].update(generation='1'),lambda x:x['binding'].update(lease_epoch=0),
    lambda x:x['binding'].update(provider='other'),lambda x:x['binding'].update(model=''),
    lambda x:x['operation'].update(kind='execute.python'),
    lambda x:x['operation'].pop('checkpoint_sha256'),
    lambda x:x['operation'].update(answer='unbound replacement'),
    lambda x:x['operation'].update(answer_ref='../private'),
    lambda x:x['operation'].update(checkpoint_sha256='bad'),
])
def test_rejects_unknown_authority_or_ambiguous_shape(change):
    value=operation();change(value)
    with pytest.raises((ValidationError,ValueError)):decode_operation(frame(value))


@pytest.mark.parametrize('wire',[
    b'{}',b'{}\n{}\n',b'[]\n',b'{"version":1,"version":1}\n',
    b'\xff\n',b' '*MAX_FRAME_BYTES+b'\n',b'{"x":NaN}\n',
])
def test_transport_rejects_invalid_frames(wire):
    with pytest.raises((ValidationError,ValueError)):decode_operation(wire)


@pytest.mark.parametrize('field,value',[
    ('owner_id','00000000-0000-0000-0000-000000000003'),
    ('auth_session_id','00000000-0000-0000-0000-000000000003'),
    ('task_id','other'),('session_id','other'),('job_id','other'),
    ('generation',2),('lease_epoch',3),('provider','ollama'),('model','other'),
])
def test_binding_requires_every_authoritative_field(field,value):
    original=decode_operation(frame(operation()));foreign=operation();foreign['binding'][field]=value
    if field=='generation':foreign['binding']['execution_anchor_sha256']='d'*64
    expected=decode_operation(frame(foreign)).binding
    with pytest.raises(ValueError,match='binding differs'):require_binding(original,expected)
    require_binding(original,original.binding)


@pytest.mark.parametrize('body',[
    {'kind':'interview.start','request_ref':'request-1'},
    {'kind':'interview.advance','checkpoint_sha256':'b'*64,'expected_sequence':0},
    {'kind':'generator.phase','phase':'srs','input_manifest_sha256':'c'*64},
])
def test_other_operations_are_explicit_data_not_callbacks(body):
    value=operation();value['operation']=body
    assert decode_operation(frame(value)).operation.kind==body['kind']


@pytest.mark.parametrize('version',[True,1.0,'1'])
def test_version_does_not_coerce_lookalikes(version):
    value=operation();value['version']=version
    with pytest.raises(ValidationError):decode_operation(frame(value))


def test_duplicate_nested_keys_and_non_finite_numbers_are_rejected():
    wire=frame(operation()).replace(b'"generation": 1',b'"generation": 1, "generation": 2')
    with pytest.raises(ValueError,match='duplicate'):decode_operation(wire)
    wire=frame(operation()).replace(b'"generation": 1',b'"generation": Infinity')
    with pytest.raises(ValueError,match='non-finite'):decode_operation(wire)


def test_contract_has_no_runtime_consumers_or_effectful_imports():
    import ast
    from pathlib import Path
    root=Path(__file__).parents[1]/'src'/'delivery_api'
    for path in root.glob('*.py'):
        if path.name=='child_execution_contract.py':continue
        assert 'child_execution_contract' not in path.read_text(),path.name
    schema=root.parents[1]/'packages/requi-child-contract/src/requi_child_contract/__init__.py'
    tree=ast.parse(schema.read_text())
    allowed={'__future__','json','typing','uuid','pydantic'}
    for node in ast.walk(tree):
        if isinstance(node,ast.Import):assert all(a.name in allowed for a in node.names)
        elif isinstance(node,ast.ImportFrom):assert node.module in allowed


def test_paid_subprocess_refusal_stays_before_any_dispatch():
    from types import SimpleNamespace
    from delivery_api import producer_admission as module
    from delivery_api.attempt_ledger import AdmissionDenied
    class Store:
        def worker_job(self,job_id):return {'task_id':'task','state':'running'}
    store=Store();gateway=SimpleNamespace(store=store,job_id='job',retail_rates=object())
    with module.worker_gateway(gateway),pytest.raises(AdmissionDenied,match='exact retail subprocess compute metering is not installed'):
        module.current_worker_gateway(store,'task')


def test_phase_one_wire_example_matches_the_same_contract():
    from pathlib import Path
    raw=(Path(__file__).parent/'fixtures'/'child-execution-v1-answer.json').read_bytes()
    message=decode_operation(raw)
    assert str(message.binding.auth_session_id)=='00000000-0000-0000-0000-000000000002'
    assert message.binding.session_id=='session-1'
    assert message.operation.answer_ref=='answer-1'


def test_later_generation_requires_installed_anchor_evidence():
    value=operation();value['binding']['generation']=2
    with pytest.raises(ValidationError,match='installed anchor'):decode_operation(frame(value))
    value['binding']['execution_anchor_sha256']='d'*64
    assert decode_operation(frame(value)).binding.execution_anchor_sha256=='d'*64


def test_encoder_revalidates_constructed_models_and_refuses_unknown_fields():
    from delivery_api.child_execution_contract import ExecutionOperation
    value=operation();value['version']=True
    bypass=ExecutionOperation.model_construct(**value)
    with pytest.raises(ValidationError):encode_operation(bypass)


@pytest.mark.parametrize('field,value', [('generation',True),('lease_epoch',None),('model','model with whitespace')])
def test_binding_never_coerces_bad_values(field,value):
    body=operation();body['binding'][field]=value
    with pytest.raises(ValidationError):decode_operation(frame(body))
