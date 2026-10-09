"""Strict raw DeepSeek usage audit normalization, no pricing or inference.

Source: https://api-docs.deepseek.com/api/create-chat-completion
Prompt tokens equal cache hits plus misses. Reasoning is output detail, never an
additional billable bucket. Missing detail is unknown, not fabricated zero.
"""
from .attempt_ledger import AdmissionDenied,_integer


def normalize(raw):
    if not isinstance(raw,dict):raise AdmissionDenied('raw provider usage required')
    required={'prompt_tokens','completion_tokens','total_tokens','prompt_cache_hit_tokens','prompt_cache_miss_tokens'}
    allowed=required|{'prompt_tokens_details','completion_tokens_details'}
    if not required.issubset(raw) or set(raw)-allowed:raise AdmissionDenied('unsupported provider usage schema')
    if any(not _integer(raw[key]) for key in required):raise AdmissionDenied('integer provider usage required')
    if raw['prompt_tokens']!=raw['prompt_cache_hit_tokens']+raw['prompt_cache_miss_tokens']:
        raise AdmissionDenied('inconsistent cache usage')
    if raw['total_tokens']!=raw['prompt_tokens']+raw['completion_tokens']:
        raise AdmissionDenied('inconsistent total usage')
    prompt=raw.get('prompt_tokens_details')
    if prompt is not None:
        if not isinstance(prompt,dict) or set(prompt)!={'cached_tokens'} or prompt['cached_tokens']!=raw['prompt_cache_hit_tokens'] or not _integer(prompt['cached_tokens']):
            raise AdmissionDenied('inconsistent cached input detail')
    reasoning=None
    completion=raw.get('completion_tokens_details')
    if completion is not None:
        if not isinstance(completion,dict) or set(completion)!={'reasoning_tokens'} or not _integer(completion['reasoning_tokens']) or completion['reasoning_tokens']>raw['completion_tokens']:
            raise AdmissionDenied('inconsistent reasoning output detail')
        reasoning=completion['reasoning_tokens']
    return {'input_tokens':raw['prompt_tokens'],'output_tokens':raw['completion_tokens'],
            'cache_hit_tokens':raw['prompt_cache_hit_tokens'],'cache_miss_tokens':raw['prompt_cache_miss_tokens'],
            'reasoning_tokens':reasoning,'reasoning_included_in_output':True,'raw_usage':dict(raw)}


def settlement_usage(raw):
    """Token categories only. Requires an explicit cache-priced ledger quote.

    Reasoning stays in raw audit as output detail, never a third billed bucket.
    """
    normalized=normalize(raw)
    return {k:normalized[k] for k in ('input_tokens','output_tokens','cache_hit_tokens','cache_miss_tokens')}
