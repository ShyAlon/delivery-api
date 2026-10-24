"""Strict raw Ollama (OpenAI-compatible) usage audit normalization, no pricing or inference.

Source: https://docs.ollama.com/api/openai-compatibility - usage carries prompt,
completion and total tokens only. Local models have no cache billing detail, so no
cache categories exist here; missing detail is unknown, not fabricated zero.
"""
from .attempt_ledger import AdmissionDenied,_integer


def normalize(raw):
    if not isinstance(raw,dict):raise AdmissionDenied('raw provider usage required')
    required={'prompt_tokens','completion_tokens','total_tokens'}
    allowed=required|{'prompt_tokens_details','completion_tokens_details'}
    if not required.issubset(raw) or set(raw)-allowed:raise AdmissionDenied('unsupported provider usage schema')
    if any(not _integer(raw[key]) for key in required):raise AdmissionDenied('integer provider usage required')
    if raw['total_tokens']!=raw['prompt_tokens']+raw['completion_tokens']:
        raise AdmissionDenied('inconsistent total usage')
    for key in ('prompt_tokens_details','completion_tokens_details'):
        detail=raw.get(key)
        if detail is not None and (not isinstance(detail,dict) or any(not _integer(n) for n in detail.values())):
            raise AdmissionDenied('integer provider usage detail required')
    return {'input_tokens':raw['prompt_tokens'],'output_tokens':raw['completion_tokens'],'raw_usage':dict(raw)}


def settlement_usage(raw):
    """Token categories only; the reviewed local row prices exactly input and output."""
    normalized=normalize(raw)
    return {k:normalized[k] for k in ('input_tokens','output_tokens')}
