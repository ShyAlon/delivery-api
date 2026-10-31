"""Actual cloud routes, restricted PG, real zero-model handoff, no paid provider."""
import hashlib,io,json,sys,uuid,zipfile
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from delivery_api.tenancy import tenant_context

FIXTURE=Path(__file__).parent/'fixtures/coding-package'


def seed(ws):
    from delivery_api.artifact_publication import document_artifact_keys,handoff_document_keys
    s=ws.store
    with tenant_context(ws.scope):
        tid=s.create_task('Cloud export '+uuid.uuid4().hex)['task_id'];sid=str(uuid.uuid4());s.set_engine_session(tid,sid)
        rows=[]
        for (phase,name),relative in (document_artifact_keys()|handoff_document_keys()|{('baseline','system-baseline.json'):'spec/system-baseline.json'}).items():
            path=FIXTURE/relative
            if path.is_file():
                value=json.loads(path.read_bytes())
                def bind(v):
                    if isinstance(v,dict):return {k:(sid if k=='session_id' and x=='s' else bind(x)) for k,x in v.items()}
                    if isinstance(v,list):return [bind(x) for x in v]
                    return v
                rows.append({'phase_key':phase,'name':name,'data':json.dumps(bind(value)).encode()})
        s.publish_task_revision_bytes(tid,rows,writer_instance='test-final-export')
        for p in s.task(tid)['phases']:s.update_phase(tid,p['phase_key'],'approved')
    return tid,rows


@pytest.fixture
def export_env(monkeypatch):
    # Built runtime uses its installed producer, the same entry point as production.
    monkeypatch.setenv('REQUI_RE_DIST','1')
    monkeypatch.setenv('VMODEL_ARTIFACTS_DIR','/does-not-exist/cloud-final-export')


def test_cloud_package_actual_create_download_repeat_tenant_and_tamper(export_env,pg_workspace_factory,pg_task_app,monkeypatch):
    with pg_workspace_factory() as ws:
        tid,rows=seed(ws);client=TestClient(pg_task_app(ws),headers=ws.headers)
        response=client.post(f'/api/tasks/{tid}/packages',json={})
        assert response.status_code==200,response.text
        result=response.json();assert result['status']=='ready' and 'path' not in result
        downloaded=client.get(result['url']);assert downloaded.status_code==200
        assert downloaded.headers['cache-control']=='private, no-store'
        with zipfile.ZipFile(io.BytesIO(downloaded.content)) as z:
            manifest=json.loads(z.read('package.json'))
            assert manifest['source']['owner_id']==ws.scope.owner_id
            assert manifest['task_id']==tid and manifest['generation']==1
            assert 'spec/AGENTS.md' in z.namelist() and 'spec/validation.json' in z.namelist()
            assert not any('checkpoint' in n or 'private' in n for n in z.namelist())
            for name,digest in manifest['sha256'].items():assert hashlib.sha256(z.read(name)).hexdigest()==digest
        repeat=client.post(f'/api/tasks/{tid}/packages',json={});assert repeat.status_code==200
        assert repeat.json()['package_id']==result['package_id']
        assert client.get(repeat.json()['url']).content==downloaded.content
        # A later publication cannot alter an already retained download.
        with tenant_context(ws.scope):ws.store.publish_task_revision_bytes(tid,rows,writer_instance='republish')
        newer=client.post(f'/api/tasks/{tid}/packages',json={});assert newer.status_code==200
        assert newer.json()['package_id']!=result['package_id']
        assert client.get(result['url']).content==downloaded.content
        assert client.get(result['url'].replace('generation=1','generation=2')).status_code==404
        with pg_workspace_factory() as other:
            foreign=TestClient(pg_task_app(other),headers=other.headers)
            assert foreign.get(result['url']).status_code==404
            assert foreign.post(f'/api/tasks/{tid}/packages',json={}).status_code==404
        # Restore actual owner app after the other fixture selected its store.
        client=TestClient(pg_task_app(ws),headers=ws.headers)
        with ws.admin() as conn:conn.execute('UPDATE final_packages SET payload=%s WHERE package_id=%s',(b'tampered',result['package_id']))
        bad=client.get(result['url']);assert bad.status_code==409 and b'tampered' not in bad.content


def test_cloud_export_refuses_missing_review_and_active_work_without_disk_fallback(export_env,pg_workspace_factory,pg_task_app):
    with pg_workspace_factory() as ws:
        tid,rows=seed(ws);client=TestClient(pg_task_app(ws),headers=ws.headers)
        with tenant_context(ws.scope):
            ws.store.publish_task_revision_bytes(tid,[r for r in rows if r['name']!='findings-ledger.json'],writer_instance='missing-review')
        reply=client.post(f'/api/tasks/{tid}/packages',json={});assert reply.status_code==409
        assert 'findings-ledger' not in reply.text and '/tmp' not in reply.text
        with ws.admin() as conn:assert conn.execute('SELECT count(*) FROM final_packages WHERE task_id=%s',(tid,)).fetchone()[0]==0


def test_export_publication_captures_review_evidence_atomically(pg_scoped_workspace):
    from delivery_api.generator_documents import GeneratorDocuments
    from delivery_api.artifact_publication import publish_document_revision
    s=pg_scoped_workspace.store
    tid=s.create_task('Publish export evidence')['task_id'];s.set_engine_session(tid,'docs-'+uuid.uuid4().hex)
    job=s.enqueue_worker(tid,'phase:validation');cap=s.claim_worker(job);docs=GeneratorDocuments(s,cap,job)
    docs.write('spec/findings-ledger.json',b'{"version":1,"findings":{},"incomplete":[],"history":[]}')
    docs.write('spec/review-history.json',b'[]');docs.write('spec/private-llm-calls.jsonl',b'private')
    publish_document_revision(s,tid,docs)
    snapshot=s.manifest_snapshot(tid);members={(r['phase_key'],r['name']):r for r in snapshot['rows']}
    assert ('handoff','findings-ledger.json') in members and ('handoff','review-history.json') in members
    assert not any('private' in name for _,name in members)
    old=members['handoff','findings-ledger.json']['sha256'];docs.write('spec/findings-ledger.json',b'new unpublished content')
    assert s.read_artifact_bytes_sha(old).startswith(b'{"version":1')


@pytest.mark.parametrize('problem',['unapproved','active_job','wrong_session','missing_baseline','open_findings','source_tamper','revision_race'])
def test_cloud_export_refuses_unsafe_final_state(export_env,pg_workspace_factory,pg_task_app,monkeypatch,problem):
    with pg_workspace_factory() as ws:
        tid,rows=seed(ws);s=ws.store
        if problem=='unapproved':
            with tenant_context(ws.scope):s.update_phase(tid,'validation','running')
        elif problem=='active_job':
            with tenant_context(ws.scope):s.enqueue_worker(tid,'phase:validation')
        elif problem in ('wrong_session','missing_baseline','open_findings'):
            if problem=='missing_baseline':rows=[r for r in rows if r['name']!='system-baseline.json']
            if problem=='wrong_session':
                row=next(r for r in rows if r['name']=='needs.json');v=json.loads(row['data']);v['session_id']='another-session';row['data']=json.dumps(v).encode()
            if problem=='open_findings':
                row=next(r for r in rows if r['name']=='findings-ledger.json');row['data']=b'{"version":1,"findings":{},"incomplete":[{"scope":"required","reason":"review interrupted"}],"history":[]}'
            with tenant_context(ws.scope):s.publish_task_revision_bytes(tid,rows,writer_instance='bad-final-state')
        elif problem=='source_tamper':
            digest=hashlib.sha256(rows[0]['data']).hexdigest()
            with ws.admin() as conn:conn.execute('UPDATE artifact_bytes SET bytes=%s WHERE owner_id=%s AND sha256=%s',(b'corrupt',ws.scope.owner_id,digest))
        else:
            real=s.manifest_snapshot;calls=[]
            def raced(*a,**kw):
                result=real(*a,**kw);calls.append(1)
                if len(calls)>1:result={**result,'revision_id':'replaced-during-export'}
                return result
            monkeypatch.setattr(s,'manifest_snapshot',raced)
        c=TestClient(pg_task_app(ws),headers=ws.headers);r=c.post(f'/api/tasks/{tid}/packages',json={})
        assert r.status_code==409,r.text
        assert 'another-session' not in r.text and 'corrupt' not in r.text and 'Traceback' not in r.text
        with ws.admin() as conn:assert conn.execute('SELECT count(*) FROM final_packages WHERE task_id=%s',(tid,)).fetchone()[0]==0


def test_packages_are_immutable_under_app_role_and_delete_hides_without_erasing(export_env,pg_workspace_factory,pg_task_app):
    import psycopg
    with pg_workspace_factory() as ws:
        tid,_=seed(ws);s=ws.store;c=TestClient(pg_task_app(ws),headers=ws.headers)
        r=c.post(f'/api/tasks/{tid}/packages',json={});assert r.status_code==200
        result=r.json()
        with tenant_context(ws.scope):
            with s._postgres.transaction() as conn:
                with pytest.raises(psycopg.errors.InsufficientPrivilege):
                    with conn.transaction():conn.execute('UPDATE final_packages SET payload=%s WHERE task_id=%s',(b'replaced',tid))
                with pytest.raises(psycopg.errors.InsufficientPrivilege):
                    with conn.transaction():conn.execute('DELETE FROM final_packages WHERE task_id=%s',(tid,))
            s.delete_task(tid)
        assert c.get(result['url']).status_code==404
        with ws.admin() as conn:assert conn.execute('SELECT count(*) FROM final_packages WHERE task_id=%s',(tid,)).fetchone()[0]==1


def test_export_holds_real_task_lock_until_bytes_are_retained(export_env,pg_workspace_factory,monkeypatch):
    import threading
    from concurrent.futures import ThreadPoolExecutor
    from delivery_api import cloud_package
    with pg_workspace_factory() as ws:
        tid,rows=seed(ws);s=ws.store;entered=threading.Event();release=threading.Event();published=threading.Event()
        real=cloud_package.build
        def paused(*args,**kwargs):entered.set();assert release.wait(8);return real(*args,**kwargs)
        monkeypatch.setattr(cloud_package,'build',paused)
        def export():
            with tenant_context(ws.scope):return s.create_final_package(tid)
        def republish():
            with tenant_context(ws.scope):s.publish_task_revision_bytes(tid,rows,writer_instance='concurrent-publisher');published.set()
        with ThreadPoolExecutor(max_workers=2) as workers:
            exporting=workers.submit(export);assert entered.wait(5)
            publishing=workers.submit(republish)
            assert not published.wait(.15)
            release.set();result=exporting.result(timeout=8);publishing.result(timeout=8)
        assert published.is_set()
        with tenant_context(ws.scope):
            raw=s.final_package(tid,result['generation'],result['package_id'])
            with zipfile.ZipFile(io.BytesIO(raw)) as z:source=json.loads(z.read('package.json'))['source']
            assert source['revision_id']!=s.manifest_snapshot(tid)['revision_id']
