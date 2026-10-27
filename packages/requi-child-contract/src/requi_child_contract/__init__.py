"""Proposed v1 child execution envelope. Inert: no launch, IO or authority grant.

Delivery constructs this from verified durable state. The eventual receiver
must independently check live job/lease/stop/review evidence; syntactic validity
or require_binding alone never authorizes execution. Provider and lifecycle
channels remain separate. No callbacks, prompt control, executable or path.
"""
from __future__ import annotations

import json
from typing import Annotated, Literal, Union
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator

MAX_FRAME_BYTES=64*1024
Ref=Annotated[str,StringConstraints(pattern=r'^[A-Za-z0-9][A-Za-z0-9_.:-]{0,199}$')]
Digest=Annotated[str,StringConstraints(pattern=r'^[a-f0-9]{64}$')]
Model=Annotated[str,StringConstraints(min_length=1,max_length=200,pattern=r'^\S+$')]
Positive=Annotated[int,Field(strict=True,ge=1)]
Sequence=Annotated[int,Field(strict=True,ge=0)]


class _Frozen(BaseModel):
    model_config=ConfigDict(extra='forbid',frozen=True,strict=True)


class JobBinding(_Frozen):
    owner_id: UUID
    auth_session_id: UUID
    task_id: Ref
    session_id: Ref
    job_id: Ref
    generation: Positive
    lease_epoch: Positive
    provider: Literal['deepseek','ollama']
    model: Model
    execution_anchor_sha256: Digest | None = None

    @model_validator(mode='after')
    def installed_anchor(self):
        if self.generation>1 and self.execution_anchor_sha256 is None:
            raise ValueError('later-generation installed anchor required')
        return self

    @field_validator('owner_id','auth_session_id',mode='before')
    @classmethod
    def uuid_value(cls,value):
        # Wire uses canonical UUID strings; Python UUIDs also round-trip.
        if isinstance(value,str):
            result=UUID(value)
            if str(result)!=value:raise ValueError('canonical UUID required')
            return result
        return value


class InterviewStart(_Frozen):
    kind: Literal['interview.start']
    request_ref: Ref


class InterviewAdvance(_Frozen):
    kind: Literal['interview.advance']
    checkpoint_sha256: Digest
    expected_sequence: Sequence


class InterviewAnswer(_Frozen):
    kind: Literal['interview.answer']
    checkpoint_sha256: Digest
    wait_id: Ref
    expected_sequence: Sequence
    review_revision: Ref
    answer_ref: Ref


class InterviewRoute(_Frozen):
    kind: Literal['interview.route']
    checkpoint_sha256: Digest
    expected_sequence: Sequence
    wait_id: Ref
    request_ref: Ref


class GeneratorPhase(_Frozen):
    kind: Literal['generator.phase']
    phase: Literal['needs','srs','architecture','items','tests','validation','review','repair','expert_opinion']
    input_manifest_sha256: Digest


Operation=Annotated[Union[InterviewStart,InterviewAdvance,InterviewAnswer,InterviewRoute,GeneratorPhase],Field(discriminator='kind')]


class ExecutionOperation(_Frozen):
    version: Literal[1]
    operation_id: Ref
    binding: JobBinding
    operation: Operation

    @field_validator('version',mode='before')
    @classmethod
    def exact_version(cls,value):
        if type(value) is not int:raise ValueError('integer version required')
        return value


def _unique_pairs(pairs):
    result={}
    for key,value in pairs:
        if key in result:raise ValueError('duplicate JSON key')
        result[key]=value
    return result


def _invalid_constant(value):
    raise ValueError('non-finite JSON constant')


def decode_operation(frame:bytes)->ExecutionOperation:
    if not isinstance(frame,bytes) or not 1<len(frame)<=MAX_FRAME_BYTES:
        raise ValueError('bounded byte frame required')
    if not frame.endswith(b'\n') or b'\n' in frame[:-1]:raise ValueError('one newline-terminated frame required')
    try:
        value=json.loads(frame.decode('utf-8'),object_pairs_hook=_unique_pairs,parse_constant=_invalid_constant)
        return ExecutionOperation.model_validate(value)
    except (UnicodeError,RecursionError) as exc:
        raise ValueError('invalid execution frame') from exc


def encode_operation(operation:ExecutionOperation)->bytes:
    # Revalidate even a model constructed without validation by internal callers.
    checked=ExecutionOperation.model_validate(operation.model_dump(mode='json'))
    frame=json.dumps(checked.model_dump(mode='json'),separators=(',',':'),allow_nan=False).encode()+b'\n'
    if len(frame)>MAX_FRAME_BYTES:raise ValueError('execution frame too large')
    return frame


def require_binding(operation:ExecutionOperation,expected:JobBinding)->None:
    """Compare against a trusted binding; does not read live state or grant authority."""
    if operation.binding!=expected:raise ValueError('execution job binding differs')


class LifecycleEvent(_Frozen):
    version: Literal[1]
    operation_id: Ref
    sequence: Positive
    state: Literal['ready','running','completed','stopped','failed']

    @field_validator('version',mode='before')
    @classmethod
    def exact_version(cls,value):
        if type(value) is not int:raise ValueError('integer version required')
        return value


def decode_event(frame:bytes)->LifecycleEvent:
    if not isinstance(frame,bytes) or not 1<len(frame)<=4096 or not frame.endswith(b'\n') or b'\n' in frame[:-1]:
        raise ValueError('bounded lifecycle frame required')
    try:
        return LifecycleEvent.model_validate(json.loads(frame.decode('utf-8'),object_pairs_hook=_unique_pairs,parse_constant=_invalid_constant))
    except (UnicodeError,RecursionError) as exc:raise ValueError('invalid lifecycle frame') from exc


def encode_event(event:LifecycleEvent)->bytes:
    checked=LifecycleEvent.model_validate(event.model_dump(mode='json'))
    return json.dumps(checked.model_dump(mode='json'),separators=(',',':')).encode()+b'\n'
