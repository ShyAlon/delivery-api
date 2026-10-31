"""PG usage evidence and derived internal rollup, no durable path dependency."""
import json,math
from .generator_documents import GeneratorDocuments

def usage_rows(store,task_id):
    reader=GeneratorDocuments.owner_reader(store,task_id);batches=[];rejected=invalid=0
    for area in ('interview','spec'):
        raw=reader.read(area+'/usage.jsonl');rows=[]
        for encoded in (raw if raw is not None else b'').splitlines():
            try:line=encoded.decode('utf-8')
            except UnicodeDecodeError:rejected+=1;continue
            if not line.strip():continue
            try:row=json.loads(line)
            except ValueError:rejected+=1;continue
            if not isinstance(row,dict):rejected+=1;continue
            bad=False
            for key in ('prompt_tokens','completion_tokens','latency_s','price_per_mtok_prompt_usd','price_per_mtok_completion_usd'):
                value=row.get(key)
                if value is None:continue
                try:
                    number=float(value)
                    if isinstance(value,bool) or not math.isfinite(number) or number<0 or (key.endswith('tokens') and not number.is_integer()):bad=True
                except (ValueError,TypeError,OverflowError):bad=True
            invalid+=int(bad);rows.append(row)
        batches.append(rows)
    return batches,{'complete':not(rejected or invalid),'rejected_rows':rejected,'invalid_numeric_rows':invalid,'scope':'PG usage evidence only'}

def update_rollup(store,task_id):
    with store._task_operation_guard('task.read'):
        store._execute_native('SELECT task_id FROM tasks WHERE task_id=%s FOR UPDATE',(task_id,)).fetchone()
        summary=usage_summary(store,task_id)
        evidence=summary['evidence'];report=summary['report']
        report['evidence']=evidence
        raw=json.dumps(report,sort_keys=True,indent=1).encode()
        reader=GeneratorDocuments.owner_reader(store,task_id)
        reader.write_internal_cost_rollup(raw)
        return evidence

def _blank():
    return dict(calls=0,unpriced_calls=0,prompt_tokens=0,completion_tokens=0,cached_tokens=0,cost_usd=0.0)

def _projection():
    return {'cursor':None,'pending':'','rejected':0,'invalid':0,'total':_blank(),'stage':{},'provider':{},'model':{}}

def _consume(state,line):
    try:
        text=line.decode('utf-8')
        if not text.strip():return
        row=json.loads(text)
        if not isinstance(row,dict):raise ValueError('usage object required')
    except (UnicodeDecodeError,ValueError):state['rejected']+=1;return
    for key in ('prompt_tokens','completion_tokens','latency_s','price_per_mtok_prompt_usd','price_per_mtok_completion_usd'):
        value=row.get(key)
        if value is None:continue
        try:
            number=float(value)
            if isinstance(value,bool) or not math.isfinite(number) or number<0 or (key.endswith('tokens') and not number.is_integer()):raise ValueError('invalid usage number')
        except (ValueError,TypeError,OverflowError):state['invalid']+=1;return
    # Keep unrounded sums. Public rounding happens once at the report boundary.
    from vmodel_contracts.telemetry import _row_cost
    cost=_row_cost(row)
    for bucket in (state['total'],*(state[k].setdefault(str(row.get(k) or 'unknown'),_blank()) for k in ('stage','provider','model'))):
        bucket['calls']+=1
        for key in ('prompt_tokens','completion_tokens','cached_tokens'):bucket[key]+=int(row.get(key) or 0)
        if cost is None:bucket['unpriced_calls']+=1
        else:bucket['cost_usd']+=cost

def usage_summary(store,task_id):
    """Durable epoch/cursor projection; replay only appended complete lines.

    The unterminated last line is evaluated for this response but never committed
    to totals until its terminator arrives. A later append can repair it exactly.
    """
    import copy,hashlib
    from psycopg.types.json import Jsonb
    from .generator_documents import document_tail
    reader=GeneratorDocuments.owner_reader(store,task_id);cap=reader.capability;states=[]
    with store._task_operation_guard('task.read'):
        store._execute_native('SELECT task_id FROM tasks WHERE task_id=%s FOR UPDATE',(task_id,)).fetchone()
        with reader.saver._cursor() as cur:
            for area in ('interview','spec'):
                key=area+'/usage.jsonl'
                saved=cur.execute('SELECT body FROM document_projections WHERE execution_session_id=%s AND document_key=%s',(cap.session_id,key)).fetchone()
                state=saved['body'] if saved else _projection()
                seed=states[0] if states else _projection()
                signature=hashlib.sha256(json.dumps(seed,sort_keys=True).encode()).hexdigest() if states else None
                # Original rollup sums interview before spec. A changed prefix
                # requires one spec replay to preserve IEEE float/round ordering.
                reseed=bool(states) and state.get('interview_signature')!=signature
                raw,cursor,reset=document_tail(cur,cap.session_id,cap.generation,key,None if reseed else state['cursor'])
                if reset or reseed:
                    state=copy.deepcopy(seed);state.update(cursor=None,pending='',interview_signature=signature)
                if raw is not None:
                    data=bytes.fromhex(state['pending'])+raw;state['pending']=''
                    lines=data.splitlines(keepends=True)
                    if lines and not lines[-1].endswith((b'\n',b'\r')):state['pending']=lines.pop().hex()
                    for line in lines:_consume(state,line)
                    state['cursor']=cursor
                    if not saved or raw or reset:
                        cur.execute('INSERT INTO document_projections(execution_session_id,document_key,owner_id,generation,body) VALUES(%s,%s,%s,%s,%s) ON CONFLICT(execution_session_id,document_key) DO UPDATE SET body=EXCLUDED.body',(cap.session_id,key,cap.scope.owner_id,cap.generation,Jsonb(state)))
                transient=copy.deepcopy(state)
                if transient['pending']:_consume(transient,bytes.fromhex(transient['pending']))
                states.append(transient)
    final=states[-1];rejected=final['rejected'];invalid=final['invalid']
    evidence={'complete':not(rejected or invalid),'rejected_rows':rejected,'invalid_numeric_rows':invalid,'scope':'PG usage evidence only'}
    from vmodel_contracts import cost_rollup
    report=cost_rollup([]);report.update(final['total'])
    for kind in ('stage','provider','model'):report['by_'+kind]=final[kind]
    for bucket in (report,*(b for kind in ('stage','provider','model') for b in report['by_'+kind].values())):bucket['cost_usd']=round(bucket['cost_usd'],8)
    interview_tokens=states[0]['total']['prompt_tokens']+states[0]['total']['completion_tokens']
    all_tokens=final['total']['prompt_tokens']+final['total']['completion_tokens']
    return {'report':report if evidence['complete'] and report['calls'] else {},'evidence':evidence,'tokens':[interview_tokens,all_tokens-interview_tokens]}
