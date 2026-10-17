"""Private connected acceptance coordinator. Explicit adapters, no default effects.

Hermetic adapters prove wiring only. mac_acceptance adapters must return verifiable
source receipts; network execution still requires a separately reviewed run go.
No application import, mutation route, fence override or automatic next-run retry.
"""
import datetime,hashlib,re

def cause_names(exc,limit=3):
    # Exception class names along the cause chain. Never messages: those can carry payloads or secrets.
    names=[];seen=set();e=exc
    while e is not None and id(e) not in seen and len(names)<limit:
        seen.add(id(e));name=type(e).__name__
        if re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]{0,60}',name):names.append(name)
        e=e.__cause__ or e.__context__
    return names


def failure_label(exc):
    names=cause_names(exc) or ['Exception']
    return 'unnamed:'+names[0]+(' caused by '+' then '.join(names[1:]) if len(names)>1 else '')


def stage_failure_label(stage,exc):
    # Label for a failure inside a generator stage, for example: tests stage failed after OpenAIConnectionError
    if not (isinstance(stage,str) and re.fullmatch(r'[a-z_]{1,30}',stage)):return None
    names=cause_names(exc)
    label=stage+' stage failed'+(' after '+' then '.join(names) if names else '')
    return label if re.fullmatch(r'[A-Za-z0-9_=. -]{1,80}',label) else stage+' stage failed'


def item_label(raw):
    # Console label for a progress item: short identifiers only, never free text.
    return raw if isinstance(raw,str) and re.fullmatch(r'[A-Za-z0-9_.:()# -]{1,60}',raw) else ''


STAGES=('identity','operator','sandbox_purchase','pipeline','gate_edit','final_artifacts','recovery')


class AcceptanceChain:
    def __init__(self,isolation,*,adapters,progress=None):
        if set(adapters)!=set(STAGES) or any(not callable(x) for x in adapters.values()):
            raise ValueError('all explicit chain adapters required; no silent skip')
        self.isolation=isolation;self.adapters=dict(adapters);self.progress=progress or (lambda line:None)
        self.root=None;self.receipts={}

    def emit(self,stage,state):
        timestamp=datetime.datetime.now().astimezone().isoformat(timespec='seconds')
        self.progress(f'{timestamp} | {stage}.{state} | Run {self.isolation.run_id}')

    def execute(self,*,run_go=None):
        if self.isolation.mode=='mac_acceptance' and run_go!=self.isolation.run_id:
            raise ValueError('explicit go bound to this exact Mac run required')
        self.root=self.isolation.initialize()
        for stage in STAGES:
            self.emit(stage,'start')
            try:
                # Pass only bounded receipt facts and scope, no env/credential dump.
                result=self.adapters[stage](self.isolation,dict(self.receipts))
                self.validate(stage,result)
                self.isolation.write(self.root/(stage+'.json'),result)
                self.receipts[stage]=result
            except Exception as exc:
                self.emit(stage,'blocked')
                # Name the failing check, never the exception text: only a short label an adapter set on purpose is shown.
                check=getattr(exc,'safe_check',None)
                if not (isinstance(check,str) and re.fullmatch(r'[A-Za-z0-9_=. -]{1,80}',check)):check=failure_label(exc)
                self.emit(stage,'check='+check)
                self.isolation.write(self.root/'blocked.json',{'stage':stage,'automatic_retry':False,'failed_check':check,
                    'result':'INCOMPLETE','launch_authorized':False,'reason':'Adapter or evidence check failed; preserve partial evidence and review locally'})
                # Don't serialize arbitrary exception text or credential-bearing payloads.
                raise RuntimeError('item6 chain incomplete at '+stage+'; no retry or launch') from None
            self.emit(stage,'pass')
        evidence={name:hashlib.sha256((self.root/(name+'.json')).read_bytes()).hexdigest() for name in STAGES}
        result={'result':'HERMETIC_WIRING_PASS' if self.isolation.mode=='hermetic' else 'ACCEPTANCE_EVIDENCE_READY',
            'run_id':self.isolation.run_id,'mode':self.isolation.mode,'evidence_sha256':evidence,
            'launch_authorized':False,'production_routes_enabled':False,'gate_edit_public_enabled':False}
        self.isolation.write(self.root/'result.json',result);self.emit('chain','complete');return result

    def validate(self,stage,r):
        if not isinstance(r,dict) or r.get('stage')!=stage or r.get('run_id')!=self.isolation.run_id:
            raise ValueError('exact stage/run receipt required')
        if r.get('source_mode')!=self.isolation.mode:raise ValueError('fixture/acceptance evidence cannot mix')
        if r.get('production_effect') is not False:raise ValueError('production effect forbidden')
        # Do not accept secret/debug payloads as evidence. Fixed projection from
        # each adapter contains only the documented fields below.
        base={'stage','run_id','source_mode','production_effect'}
        fields={
            'identity':{'subject','session_id','signature_verified','active_session','fresh_signup'},
            'operator':{'subject','registry_version','explicit_grant','capabilities'},
            'sandbox_purchase':{'subject','purchase_id','payment_id','merchant_id','sandbox','amount_minor','credit_units','posted_count','signature_verified','balance_units'},
            'pipeline':{'subject','task_id','session_id','model_provider','model','job_ids','canonical_validated','artifact_sha256','all_stages_complete','usage_settled'},
            'gate_edit':{'task_id','owner_id','source_review_sha256','fresh_review_sha256','stale_rejected','same_phase_reapproval','source_preserved','installed_lineage_verified'},
            'final_artifacts':{'task_id','owner_id','session_id','files_sha256','download_verified','canonical_validated'},
            'recovery':{'task_id','owner_id','quiescent','unknown_attempts','blind_replays','foreign_owner_denied'},
        }
        if set(r)!=base|fields[stage]:raise ValueError('fixed safe receipt projection required')
        def true(*keys):
            if any(r.get(k) is not True for k in keys):raise ValueError('verified receipt condition required')
        def digest(value):
            if not isinstance(value,str) or not re.fullmatch(r'[a-f0-9]{64}',value):raise ValueError('content digest required')
        def inventory(value):
            if not isinstance(value,dict) or not value:raise ValueError('complete artifact inventory required')
            for k,v in value.items():
                if not isinstance(k,str) or k.startswith('/') or '..' in k.split('/'):raise ValueError('safe artifact identity required')
                digest(v)
        if stage=='identity':
            from uuid import UUID
            if str(UUID(r['subject']))!=r['subject'] or str(UUID(r['session_id']))!=r['session_id']:raise ValueError('verified canonical identity required')
            true('signature_verified','active_session','fresh_signup')
        else:
            identity=self.receipts['identity']
            if ('subject' in r and r['subject']!=identity['subject']) or ('owner_id' in r and r['owner_id']!=identity['subject']):raise ValueError('owner continuity required')
        if stage=='operator':
            true('explicit_grant')
            if r['subject']!=self.receipts['identity']['subject'] or not r['registry_version'] or not r['capabilities']:raise ValueError('explicit operator binding required')
        elif stage=='sandbox_purchase':
            true('sandbox','signature_verified')
            units=12000 if self.isolation.mode=='hermetic' else 12*10**9
            if (r['amount_minor'],r['credit_units'],r['posted_count'],r['balance_units'])!=(1150,units,1,units):raise ValueError('exact 12-credit sandbox purchase, once-only posting required')
            if not all(isinstance(r[k],str) and r[k] for k in ('purchase_id','payment_id','merchant_id')):raise ValueError('durable payment bindings required')
        elif stage=='pipeline':
            true('canonical_validated','all_stages_complete','usage_settled')
            if r['model_provider']!='deepseek' or not r['model'] or not r['job_ids']:raise ValueError('effective DeepSeek jobs required')
            if r['session_id']!=self.receipts['identity']['session_id'] and not r['session_id']:raise ValueError('owned engine session required')
            inventory(r['artifact_sha256'])
        elif stage in ('gate_edit','final_artifacts','recovery'):
            if r['task_id']!=self.receipts['pipeline']['task_id']:raise ValueError('same task chain required')
            if stage=='gate_edit':
                true('stale_rejected','same_phase_reapproval','source_preserved','installed_lineage_verified')
                digest(r['source_review_sha256']);digest(r['fresh_review_sha256'])
                if r['source_review_sha256']==r['fresh_review_sha256']:raise ValueError('fresh same-phase review required')
            elif stage=='final_artifacts':
                true('download_verified','canonical_validated');inventory(r['files_sha256'])
                if r['session_id']!=self.receipts['pipeline']['session_id']:raise ValueError('same owned execution session required')
                if not set(self.receipts['pipeline']['artifact_sha256'])<=set(r['files_sha256']):raise ValueError('pipeline inventory missing from download')
            else:
                true('quiescent','foreign_owner_denied')
                if r['unknown_attempts']!=0 or r['blind_replays']!=0:raise ValueError('uncertain work is not clean-run success')
