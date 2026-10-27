"""Compatibility import. The neutral package is the sole wire-schema source.

The pinned vendored wheel is shared with interview-engine. Parsing never
supplies owner authority and this module installs no runtime consumer.
"""
from requi_child_contract import (  # noqa: F401
    MAX_FRAME_BYTES, Ref, Digest, Model, Positive, Sequence, JobBinding,
    InterviewStart, InterviewAdvance, InterviewAnswer, GeneratorPhase,
    Operation, ExecutionOperation, decode_operation, encode_operation, require_binding,
)
