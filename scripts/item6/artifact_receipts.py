"""Read source-of-truth private outputs; don't accept a completion Boolean."""
import hashlib,json
from pathlib import Path
from delivery_api.tenancy import tenant_context,session_root
from delivery_api.worker_capability import WorkerCapability
from delivery_api.phases import PHASE_DEFINITIONS
from private_adapters import base


def inventory(root):
    from vmodel_contracts import IntentModel,ConOpsDocument,StakeholderNeedsSet,SRSDocument,ArchitectureOverview,SpecManifest
    # Exact canonical names. Alias export files are not completion substitutes.
    validators={'interview/intent_model.json':IntentModel,'interview/conops.json':ConOpsDocument,
        'spec/needs.json':StakeholderNeedsSet,'spec/srs.json':SRSDocument,'spec/architecture.json':ArchitectureOverview,
        'spec/manifest.json':SpecManifest}
    required=set(validators)|{'spec/items.json','spec/tests.json','spec/validation.json','spec/traceability.json','spec/AGENTS.md'}
    result={};documents={}
    for name in sorted(required):
        path=root/name
        if path.is_symlink() or path.resolve()!=path or not path.is_file():raise ValueError('complete regular canonical output required')
        if path.stat().st_size>32*1024*1024:raise ValueError('bounded output required')
        raw=path.read_bytes()
        if not raw:raise ValueError('nonempty canonical output required')
        if name.endswith('.json'):
            body=json.loads(raw);documents[name]=body
            if name in validators:validators[name].model_validate(body)
        result[name]=hashlib.sha256(raw).hexdigest()
    from vmodel_contracts import ItemSpec,AcceptanceTest,TraceabilityEntry,RequirementSet
    from vmodel_contracts.bundle_integrity import validate_traceability_graph
    items=[ItemSpec.model_validate(x) for x in documents['spec/items.json']]
    tests=[AcceptanceTest.model_validate(x) for x in documents['spec/tests.json']]
    trace=[TraceabilityEntry.model_validate(x) for x in documents['spec/traceability.json']]
    srs=SRSDocument.model_validate(documents['spec/srs.json'])
    needs=StakeholderNeedsSet.model_validate(documents['spec/needs.json'])
    if validate_traceability_graph(needs,RequirementSet(session_id=srs.session_id,requirements=srs.functional+srs.non_functional+srs.constraints),items,tests,trace):
        raise ValueError('bundle traceability incomplete')
    return result,documents


class PipelineReceiptAdapter:
    def __init__(self,identity,*,store,artifacts_base,execute_pipeline,read_completed_jobs,read_usage):
        self.identity=identity;self.store=store;self.artifacts_base=Path(artifacts_base)
        self.execute=execute_pipeline;self.jobs=read_completed_jobs;self.usage=read_usage
    def __call__(self,isolation,previous):
        with tenant_context(self.identity.scope):
            task_id=self.execute(isolation,self.identity.scope)
            cap=WorkerCapability.capture(self.store,task_id);cap.validate(self.store)
            root=session_root(self.artifacts_base,self.identity.scope,cap.session_id)
            files,documents=inventory(root)
            task=self.store.task(task_id)
            if any(p['state'] not in ('done','approved') for p in task['phases']):raise ValueError('all durable phase states must be done')
            jobs=self.jobs(task_id)
            if not jobs or any(j['state']!='done' or j['task_id']!=task_id or str(j['owner_id'])!=cap.scope.owner_id or j['generation']!=cap.generation for j in jobs):raise ValueError('same-owner completed worker jobs required')
            if hasattr(self.store,'retail_compute_status'):
                compute=self.store.retail_compute_status(task_id)
                if compute.get('pending') or compute.get('unresolved'):raise ValueError('compute accounting incomplete')
            usage=self.usage(task_id)
            if not usage or any(x.get('input_estimate_overrun') or x['state']!='settled' or x['expense_actual'] is None or x['retail_actual'] is None or x['provider']!='deepseek' for x in usage):raise ValueError('settled actual DeepSeek usage required')
            models={u['model'] for u in usage}
            if len(models)!=1:raise ValueError('exact selected effective model required')
            if task['model_default']!='deepseek:'+next(iter(models)):raise ValueError('effective provider differs from selected model')
            for name,body in documents.items():
                if isinstance(body,dict) and 'session_id' in body and body['session_id']!=cap.session_id:
                    raise ValueError('artifact belongs to another session')
            return base(isolation,'pipeline')|{'subject':cap.scope.owner_id,'task_id':task_id,'session_id':cap.session_id,
                'model_provider':'deepseek','model':next(iter(models)),'job_ids':[j['job_id'] for j in jobs],
                'canonical_validated':True,'artifact_sha256':files,'all_stages_complete':True,'usage_settled':True}
