"""Estimated local model memory, never container-memory based admission.

Reviewed shapes: Qwen/Qwen3-8B and Qwen/Qwen3.5-9B config.json. fp16 KV,
one sequence, no vision inputs; runtime/allocator reserve is an app estimate.
A positive estimate does not guarantee a load or reserve host memory.
"""
import json,math,time,stat
from pathlib import Path
from urllib.parse import urlsplit

GIB=1024**3
SNAPSHOT='/run/requi-host/memory.json'
SHAPES={
    'qwen3:8b':{'attention_layers':36,'kv_heads':8,'head_dim':128,'recurrent_bytes':0},
    'qwen3.5:9b':{'attention_layers':8,'kv_heads':4,'head_dim':256,
        # 24 recurrent layers, 32 value heads,128x128 fp32 state plus conv state.
        'recurrent_bytes':24*(32*128*128*4+(2*16*128+32*128)*4*4)},
}


def host_snapshot(path=SNAPSHOT,base='',*,now=None):
    # Only the known native-Mac bridge can use this launcher's telemetry.
    # A host-name match alone on an arbitrary port is not enough.
    try:
        url=urlsplit(base)
        if url.scheme!='http' or url.hostname!='host.docker.internal' or url.port!=11434:return None
        if url.username or url.password or url.query or url.fragment or url.path.rstrip('/') not in ('','/v1'):return None
        p=Path(path)
        metadata=p.stat()
        if p.is_symlink() or not stat.S_ISREG(metadata.st_mode) or metadata.st_size>4096:return None
        value=json.loads(p.read_bytes())
        if type(value) is not dict:return None
        if value.get('schema')!=1 or value.get('source')!='macos-launcher' or value.get('platform')!='Darwin' or value.get('machine')!='arm64':return None
        at=value['sampled_at'];age=(time.time() if now is None else now)-at
        if type(at) not in (int,float) or not math.isfinite(at) or not 0<=age<30:return None
        total=value['total_bytes'];available=value['available_estimate_bytes']
        if type(total) is not int or type(available) is not int or not 0<=available<=total or total<=0:return None
        return value
    except (OSError,ValueError,KeyError,TypeError):return None


def estimate(model,context,file_bytes):
    shape=SHAPES.get(model)
    if not shape or type(context) is not int or context<=0 or type(file_bytes) is not int or file_bytes<=0:return None
    kv=2*shape['attention_layers']*shape['kv_heads']*shape['head_dim']*2*context
    # Disk package bytes proxy weight bytes, not a measured resident working set.
    working=file_bytes+kv+shape['recurrent_bytes']+GIB
    return {'estimated':True,'weight_estimate_bytes':file_bytes,'kv_estimate_bytes':kv,
        'recurrent_estimate_bytes':shape['recurrent_bytes'],'runtime_reserve_bytes':GIB,
        'working_estimate_bytes':working,'assumed_parallel_sequences':1,
        'assumed_kv_cache':'fp16','context_tokens':context}


def readiness(model,context,file_bytes,snapshot,loaded):
    plan=estimate(model,context,file_bytes)
    unknown={'state':'memory_not_checked','blocked':False,'message':'Host memory not checked. Memory fit is not confirmed.'}
    if plan is None or snapshot is None:return {**unknown,**(plan or {})}
    # Only credit an already resident exact model at the selected context.
    resident=next((r.get('resident_bytes') for r in loaded if r.get('id')==model and r.get('context_length')==context and type(r.get('resident_bytes')) is int and r['resident_bytes']>0),0)
    incremental=max(0,plan['working_estimate_bytes']-resident)
    # Reserve OS/other-app headroom; use one unified-memory capacity, never
    # add size_vram to the resident size or CPU capacity to GPU capacity.
    headroom=max(GIB,snapshot['total_bytes']//10)
    budget=max(0,min(snapshot['available_estimate_bytes'],snapshot['total_bytes']-headroom))
    blocked=incremental>budget
    def gib(n):return f'{n/GIB:.1f} GiB'
    message=(f"Estimated additional model memory {gib(incremental)} exceeds current estimated available host memory {gib(budget)}. Close other applications or choose another model. No model was loaded." if blocked else
        f"Estimated model memory {gib(plan['working_estimate_bytes'])}; current host memory has estimated headroom. This is not a guaranteed fit.")
    if resident:message+=f' Ollama currently reports {gib(resident)} loaded at {context} context tokens.'
    return {**plan,'state':'estimated_unfit' if blocked else 'estimate_with_headroom','blocked':blocked,'message':message,
        'incremental_estimate_bytes':incremental,'available_estimate_bytes':budget}
