"""Source-read artifact export and owner-scoped recovery, no automatic replay."""
import hashlib,json,os,uuid,zipfile
from pathlib import Path
from delivery_api.tenancy import tenant_context,session_root,TenantScope
from artifact_receipts import inventory
from private_adapters import base


class EvidenceReader:
    def __init__(self,store):self.store=store
    def jobs(self,task_id):
        with self.store._postgres.transaction() as c:
            return [dict(x) for x in c.execute('SELECT * FROM worker_jobs WHERE task_id=%s ORDER BY created_at,job_id',(task_id,)).fetchall()]
    def usage(self,task_id):
        with self.store._postgres.transaction() as c:
            rows=c.execute('SELECT p.* FROM provider_attempts p JOIN worker_jobs j ON p.job_id=j.job_id WHERE j.task_id=%s ORDER BY p.created_at,p.attempt_id',(task_id,)).fetchall()
        result=[]
        for row in rows:
            quote=row['quote'] if isinstance(row['quote'],dict) else json.loads(row['quote'])
            result.append(dict(row)|{'provider':quote['provider'],'model':quote['model']})
        return result


class FinalArtifactsAdapter:
    def __init__(self,identity,*,store,artifacts_base):self.identity=identity;self.store=store;self.base=Path(artifacts_base)
    def __call__(self,isolation,previous):
        # Export the original complete graph and exact approved revision evidence
        # separately. Installation invalidates downstream work; never pretend a
        # staged partial revision is a newly completed end-to-end bundle.
        pipeline=previous['pipeline'];tid=pipeline['task_id'];scope=self.identity.scope
        with tenant_context(scope):
            task=self.store.task(tid)
            if not task or task['owner_id']!=scope.owner_id:raise ValueError('current owned task required')
            root=session_root(self.base,scope,pipeline['session_id']);files,_=inventory(root)
            if files!=pipeline['artifact_sha256']:raise ValueError('original completed bundle drift')
            exports={name:(root/name).read_bytes() for name in files}
            # Include the new-generation outputs and their DB-retained receipts,
            # not only the original complete bundle.
            current=session_root(self.base,scope,task['engine_session_id'])
            for row in self.store.task_artifacts(tid,include_stale=False):
                path=Path(row['ref'])
                if path.is_symlink() or path.resolve()!=path or not path.is_relative_to(current) or not path.is_file():
                    raise ValueError('current generation artifact must remain in exact owned namespace')
                name='revised/'+path.relative_to(current).as_posix();exports[name]=path.read_bytes()
            exports['revised/execution-generation.json']=(current/'execution-generation.json').read_bytes()
            with self.store._postgres.transaction() as c:
                rows=c.execute('SELECT installation_id FROM closed_revision_installations WHERE task_id=%s',(tid,)).fetchall()
            for row in rows:
                status=self.store.reconcile_private_revision_installation(tid,installation_id=row['installation_id'],artifacts_base=self.base)
                if status['state']!='installed_verified_no_dispatch' or status['unverified']:raise ValueError('revision installation proof unresolved')
                exports['revised/installation-proof.json']=json.dumps(status,sort_keys=True).encode()
            expected={name:hashlib.sha256(raw).hexdigest() for name,raw in exports.items()}
            target=Path(isolation.root)/'reviewed-artifacts.zip'
            fd=os.open(target,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
            with os.fdopen(fd,'wb') as out,zipfile.ZipFile(out,'w',compression=zipfile.ZIP_DEFLATED) as archive:
                for name,raw in sorted(exports.items()):archive.writestr(name,raw)
            with zipfile.ZipFile(target) as archive:
                downloaded={name:hashlib.sha256(archive.read(name)).hexdigest() for name in archive.namelist()}
            if downloaded!=expected:raise ValueError('download readback differs from complete canonical inventory')
            return base(isolation,'final_artifacts')|{'task_id':tid,'owner_id':scope.owner_id,'session_id':pipeline['session_id'],
                'files_sha256':downloaded,'download_verified':True,'canonical_validated':True}


class RecoveryAdapter:
    def __init__(self,identity,*,store,read_evidence):self.identity=identity;self.store=store;self.evidence=read_evidence
    def __call__(self,isolation,previous):
        from spec_generator.stop_fence import StopFence
        scope=self.identity.scope;tid=previous['pipeline']['task_id']
        with tenant_context(scope):
            task=self.store.task(tid)
            if not task or self.store.recoverable_worker_jobs():raise ValueError('unresolved owned jobs require review, never retry')
            if hasattr(self.store,'retail_compute_status'):
                compute=self.store.retail_compute_status(tid)
                if compute.get('pending') or compute.get('unresolved'):raise ValueError('unknown compute accounting requires review')
            usage=self.evidence.usage(tid)
            if not usage or any(x['state']!='settled' or x.get('input_estimate_overrun') for x in usage):raise ValueError('unknown provider attempts require review')
            jobs=self.evidence.jobs(tid)
            if any(x['attempt']!=1 or x['state']!='done' for x in jobs):raise ValueError('clean once-claimed completed jobs required')
            with self.store._postgres.transaction() as c:
                installed=c.execute('SELECT installation_id FROM closed_revision_installations WHERE task_id=%s',(tid,)).fetchall()
            for row in installed:
                result=self.store.reconcile_private_revision_installation(tid,installation_id=row['installation_id'],artifacts_base=Path(isolation.root))
                if result['state']!='installed_verified_no_dispatch' or result['unverified']:raise ValueError('installed evidence ambiguous')
        # An authenticated but unrelated identity has no authority on these rows.
        with tenant_context(TenantScope(str(uuid.uuid4()),str(uuid.uuid4()))):
            denied=self.store.task(tid) is None and not self.store.recoverable_worker_jobs()
        if not denied:raise ValueError('foreign owner unexpectedly saw work')
        return base(isolation,'recovery')|{'task_id':tid,'owner_id':scope.owner_id,'quiescent':True,
            'unknown_attempts':0,'blind_replays':0,'foreign_owner_denied':True}
