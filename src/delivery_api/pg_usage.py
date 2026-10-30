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
    from vmodel_contracts import cost_rollup
    with store._task_operation_guard('task.read'):
        store._execute_native('SELECT task_id FROM tasks WHERE task_id=%s FOR UPDATE',(task_id,)).fetchone()
        batches,evidence=usage_rows(store,task_id)
        report=cost_rollup(batches[0]+batches[1]) if evidence['complete'] and any(batches) else {}
        report['evidence']=evidence
        raw=json.dumps(report,sort_keys=True,indent=1).encode()
        reader=GeneratorDocuments.owner_reader(store,task_id)
        reader.write_internal_cost_rollup(raw)
        return evidence
