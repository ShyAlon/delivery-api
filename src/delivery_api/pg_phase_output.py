"""Generation-bound live output. Documents and published bytes, never paths."""
import json
from .generator_documents import GeneratorDocuments
from .phases import PHASE_BY_KEY,PHASE_DEFINITIONS,PHASE_POSITIONS

PARTIALS={'srs':'srs_fills.partial.json','architecture':'architecture.partial.json','specs':'items.partial.json','tests':'tests_fills.partial.json','validation':'validation_fills.partial.json'}

def phase_output(store,task):
    phase=task.get('active_phase');generation=task['generation']
    empty={'phase_key':phase,'generation':generation,'status':'waiting','items':[]}
    if phase not in PHASE_BY_KEY or not task.get('engine_session_id'):return empty
    binding=store.session_binding(task['engine_session_id'])
    if not binding or binding['generation']!=generation or task.get('stop_requested_at') is not None:return empty
    reader=GeneratorDocuments.owner_reader(store,task['task_id'])
    snapshot=store.manifest_snapshot(task['task_id'],generation)
    records=(snapshot or {}).get('rows',[])
    states={p['phase_key']:p.get('state') for p in task.get('phases',[])}
    active=('running','awaiting_approval','approved','done','completed')
    def final_for(key):
        return next((r for r in records if r['phase_key']==key and r['name'] in PHASE_BY_KEY[key].artifacts and not r['stale'] and r['valid'] is True and states.get(key) in active and r['created_at']>=float(next((p.get('started_at') or 0 for p in task.get('phases',[]) if p['phase_key']==key),0))),None)
    final=final_for(phase);display=phase;source=final['name'] if final else PARTIALS.get(phase)
    raw=store.read_artifact_bytes_sha(final['sha256']) if final else reader.read_fresh('spec/'+source,not_before=float(next((p.get('started_at') or 0 for p in task.get('phases',[]) if p['phase_key']==phase),0))) if source and states.get(phase) in active else None
    invalid=any(r['phase_key']==phase and r['valid'] is False and not r['stale'] and states.get(phase) in active and r['created_at']>=float(next((p.get('started_at') or 0 for p in task.get('phases',[]) if p['phase_key']==phase),0)) for r in records)
    try:data=json.loads(raw) if raw is not None else None
    except (ValueError,UnicodeDecodeError):return {**empty,'status':'unavailable'}
    if not final and (not data or phase=='architecture' and isinstance(data,dict) and not any(data.get(k) for k in ('stack_entries','component_bodies','adr_bodies'))):data=None
    if data is None:
        if invalid:return {**empty,'status':'unavailable'}
        position=PHASE_POSITIONS[phase]
        prior=PHASE_DEFINITIONS[position-1].key if position else None
        final=final_for(prior) if prior else None
        if final is None:return empty
        display=prior;source=final['name']
        try:data=json.loads(store.read_artifact_bytes_sha(final['sha256']))
        except (ValueError,UnicodeDecodeError):return {**empty,'status':'unavailable'}
    if final:
        if display=='srs':rows=[*data.get('functional',[]),*data.get('non_functional',[])] if isinstance(data,dict) else []
        elif display=='needs':rows=data.get('needs',[]) if isinstance(data,dict) else []
        elif display=='architecture':rows=[*data.get('decisions',[]),*data.get('components',[])] if isinstance(data,dict) else []
        elif display in ('specs','tests','validation'):rows=data if isinstance(data,list) else data.get('items',[]) if isinstance(data,dict) else []
        elif display=='conops':rows=[*data.get('operational_scenarios',[]),*data.get('concept_options',[])] if isinstance(data,dict) else []
        else:rows=[data] if isinstance(data,dict) else []
    elif phase=='architecture' and isinstance(data,dict):
        rows=[{'id':str(identity),**value} for key in ('stack_entries','component_bodies','adr_bodies') for identity,value in (data.get(key) or {}).items() if isinstance(value,dict)]
    elif phase=='specs' and isinstance(data,list):rows=data
    elif phase=='validation' and isinstance(data,dict):rows=[item for value in data.values() if isinstance(value,dict) for item in value.get('items') or [] if isinstance(item,dict)]
    elif phase in ('srs','tests') and isinstance(data,dict):rows=[{'id':key,**value} for key,value in data.items() if isinstance(value,dict)]
    else:rows=[]
    if not isinstance(rows,list):rows=[]
    if not final and not rows:return empty
    return {'phase_key':phase,'display_phase':display,'generation':generation,'source':source,
        'status':'previous_phase' if display!=phase else 'complete' if final else 'in_progress','items':[r for r in rows if isinstance(r,dict)][:500]}
