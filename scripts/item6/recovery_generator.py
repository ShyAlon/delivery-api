"""Offline generator checkpoint binding; approval is separate from preparation."""
import json,sqlite3,shutil,tempfile,hashlib
from pathlib import Path
from recovery_manifest import tree_digest


def generator_review(root,session_id):
    """Copy DB including WAL so preview cannot write original checkpoints."""
    root=Path(root);out=root/'spec'
    names=('system-baseline.json','needs.json','srs.json','checkpoints.sqlite')
    if any(not (out/name).is_file() or (out/name).is_symlink() for name in names):raise ValueError('complete preceding generator evidence required')
    from langgraph.checkpoint.sqlite import SqliteSaver
    from spec_generator.graph import checkpoint_serde
    with tempfile.TemporaryDirectory() as t:
        for name in ('checkpoints.sqlite','checkpoints.sqlite-wal','checkpoints.sqlite-shm'):
            p=out/name
            if p.exists():shutil.copy2(p,Path(t)/name)
        with sqlite3.connect(Path(t)/'checkpoints.sqlite') as c:
            saver=SqliteSaver(c,serde=checkpoint_serde());saver.is_setup=True
            saved=saver.get_tuple({'configurable':{'thread_id':session_id}})
            if saved is None:raise ValueError('saved generator checkpoint required')
            state=saved.checkpoint['channel_values']
            for channel,file in (('intent_model','interview/intent_model.json'),('conops','interview/conops.json'),('system_baseline','spec/system-baseline.json'),('needs','spec/needs.json'),('srs','spec/srs.json')):
                observed=json.loads((root/file).read_text())
                expected=state.get(channel)
                if channel=='needs':expected={**expected,'session_id':session_id}
                if channel=='srs':
                    from spec_generator.graph import srs_document_data
                    expected=srs_document_data(state['intent_model'],{**expected,'session_id':session_id,'intent_model_ref':session_id})
                from spec_generator.cli import _stamped
                if channel in ('needs','srs'):expected=_stamped(expected)
                if expected!=observed:raise ValueError('saved graph differs from preceding artifact: '+channel)
            # Raw checkpoint + pending writes hashes include partial task outputs.
            def plain(v):
                if hasattr(v,'model_dump'):return v.model_dump(mode='json')
                if isinstance(v,dict):return {k:plain(x) for k,x in v.items()}
                if isinstance(v,(list,tuple)):return [plain(x) for x in v]
                return v
            encoded=json.dumps(plain({'checkpoint':saved.checkpoint,'pending_writes':saved.pending_writes}),sort_keys=True,separators=(',',':')).encode()
            part=out/'architecture.partial.json';tests_part=out/'tests_fills.partial.json'
            # Present only after the tests stage started: pins those partial fills to the review so a rerun cannot drift.
            tests_extra={'tests_partials_present':True,'tests_partial_sha256':hashlib.sha256(tests_part.read_bytes()).hexdigest()} if tests_part.is_file() else {}
            review_part=out/'review.partial.json'
            # Review is the next step only when tests and validation are saved and review already saved some chunks; they are pinned to the review too.
            review_extra={'review_partials_present':True,'review_partial_sha256':hashlib.sha256(review_part.read_bytes()).hexdigest()} if review_part.is_file() and (out/'tests.json').is_file() and (out/'validation.json').is_file() and not tests_part.is_file() else {}
            # Review finished and its findings sit in the ledger awaiting a decision: pins the ledger so the decision cannot drift.
            ledger=state.get('findings_ledger')
            complete_extra={'review_complete_present':True,'ledger_sha256':hashlib.sha256(json.dumps(plain(ledger),sort_keys=True,separators=(',',':')).encode()).hexdigest()} if ledger is not None and not tests_extra and not review_extra and (out/'tests.json').is_file() and (out/'validation.json').is_file() and not review_part.is_file() else {}
            return {'checkpoint_id':saved.config['configurable']['checkpoint_id'],'checkpoint_sha256':hashlib.sha256(encoded).hexdigest(),'source_tree_sha256':tree_digest(root),'architecture_partials_present':part.is_file(),'architecture_partial_sha256':hashlib.sha256(part.read_bytes()).hexdigest() if part.is_file() else None,'architecture_stage_reexecution_possible':not part.is_file(),'preceding_artifact_sha256':{name:hashlib.sha256((root/name).read_bytes()).hexdigest() for name in ('interview/intent_model.json','interview/conops.json','spec/system-baseline.json','spec/needs.json','spec/srs.json')},'preceding_artifacts_unchanged':True,'provider_contacted':False,**tests_extra,**review_extra,**complete_extra}
