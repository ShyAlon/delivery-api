import pytest
from delivery_api.store import SQLiteStore
from delivery_api.gate_revision import save_closed_revision


def test_save_closed_revision_is_only_draft_no_state_or_artifact_mutation(tmp_path):
    s=SQLiteStore(str(tmp_path/'test.db'));t=s.create_task('fake');tid=t['task_id']
    s.wait_at_gate(tid,'needs');waiting=s.task(tid);s.decide_gate(tid,'gate-needs','approve',selected_option='Proceed')
    revision='a'*64;s.retain_decision_review(tid,'gate:gate-needs',revision,{'task':waiting})
    before=s.task(tid);events=s.events(tid);audit=s.audit_entries(tid)
    result=save_closed_revision(s,tid,'gate-needs',revision,'proposed wording')
    assert not result['committed'] and not result['dispatch_started']
    assert result['preview']['requires_explicit_approve_and_restart']
    assert result['preview']['restart_phase_on_commit']=='srs'
    assert s.task(tid)==before and s.events(tid)==events and s.audit_entries(tid)==audit
    assert s.decision_draft(tid,'gate:gate-needs',revision,result['draft_id'])['edited_text']=='proposed wording'
    with pytest.raises(ValueError):save_closed_revision(s,tid,'gate-needs','b'*64,'different')


def test_old_gate_basis_cannot_save_revision_after_second_approval(tmp_path):
    s=SQLiteStore(str(tmp_path/'test.db'));tid=s.create_task('fake')['task_id']
    s.wait_at_gate(tid,'needs');waiting=s.task(tid);s.retain_decision_review(tid,'gate:gate-needs','a'*64,{'task':waiting})
    s.decide_gate(tid,'gate-needs','approve',selected_option='Proceed')
    s.wait_at_gate(tid,'needs');s.decide_gate(tid,'gate-needs','approve',selected_option='Proceed')
    with pytest.raises(ValueError):save_closed_revision(s,tid,'gate-needs','a'*64,'outdated proposal')


def test_closed_revision_rejects_different_generation_and_stop(tmp_path):
    s=SQLiteStore(str(tmp_path/'test.db'));tid=s.create_task('fake')['task_id']
    s.wait_at_gate(tid,'needs');waiting=s.task(tid);s.decide_gate(tid,'gate-needs','approve',selected_option='Proceed')
    old={**waiting,'generation':waiting['generation']-1}
    s.retain_decision_review(tid,'gate:gate-needs','old',{'task':old})
    with pytest.raises(ValueError,match='generation'):save_closed_revision(s,tid,'gate-needs','old','wrong generation')


def test_closed_revision_route_saves_only_owner_review_draft(tmp_path,monkeypatch):
    from tests.test_decision_pages import setup
    s,tid,engine,c=setup(tmp_path,monkeypatch)
    s.wait_at_gate(tid,'needs');waiting=s.task(tid);rev='gate:approved-test'
    s.retain_decision_review(tid,'gate:gate-needs',rev,{'task':waiting})
    s.decide_gate(tid,'gate-needs','approve',selected_option='Proceed')
    before=s.task(tid);events=s.events(tid)
    reply=c.post(f'/api/tasks/{tid}/gates/gate-needs/revision-drafts',json={'review_revision':rev,'edited_text':'proposed revision'})
    assert reply.status_code==200,reply.text
    assert reply.json()['committed'] is False and reply.json()['dispatch_started'] is False
    assert s.task(tid)==before and s.events(tid)==events
    assert c.post(f'/api/tasks/{tid}/gates/gate-needs/revision-drafts',json={'review_revision':'missing','edited_text':'invalid'}).status_code==409


def _closed_preflight_fixture(tmp_path):
    from delivery_api.gate_revision import closed_revision_preflight
    s=SQLiteStore(str(tmp_path/'review.db'));tid=s.create_task('preflight')['task_id']
    s.wait_at_gate(tid,'needs');waiting=s.task(tid);revision='basis'
    s.retain_decision_review(tid,'gate:gate-needs',revision,{'task':waiting})
    s.decide_gate(tid,'gate-needs','approve',selected_option='Proceed')
    draft=save_closed_revision(s,tid,'gate-needs',revision,'proposed wording')['draft_id']
    evidence=[{'phase':'needs','name':'needs.json','generation':s.task(tid)['generation'],'sha256':'a'*64}]
    return s,tid,revision,draft,evidence,closed_revision_preflight


def test_preflight_is_non_effectful_binds_proposal_and_current_artifact_digest(tmp_path):
    s,tid,rev,draft,evidence,preflight=_closed_preflight_fixture(tmp_path)
    before=s.task(tid);events=s.events(tid);audit=s.audit_entries(tid)
    result=preflight(s,tid,'gate-needs',rev,draft,artifact_snapshot=evidence)
    assert not result['commit_available'] and not result['effect_executed'] and not result['dispatch_started']
    assert result==preflight(s,tid,'gate-needs',rev,draft,artifact_snapshot=evidence)
    changed=[evidence[0]|{'sha256':'b'*64}]
    assert preflight(s,tid,'gate-needs',rev,draft,artifact_snapshot=changed)['review_digest']!=result['review_digest']
    assert s.task(tid)==before and s.events(tid)==events and s.audit_entries(tid)==audit
    s.wait_at_gate(tid,'needs');s.decide_gate(tid,'gate-needs','approve',selected_option='Proceed')
    with pytest.raises(ValueError):preflight(s,tid,'gate-needs',rev,draft,artifact_snapshot=evidence)


@pytest.mark.parametrize('change',[{'phase':'srs'},{'generation':True},{'generation':99},
    {'name':'../needs.json'},{'sha256':'bad'},{'extra':True}])
def test_preflight_rejects_bad_artifact_basis(tmp_path,change):
    s,tid,rev,draft,evidence,preflight=_closed_preflight_fixture(tmp_path)
    with pytest.raises(ValueError):preflight(s,tid,'gate-needs',rev,draft,artifact_snapshot=[evidence[0]|change])


def test_preflight_rejects_missing_duplicate_and_other_draft(tmp_path):
    s,tid,rev,draft,evidence,preflight=_closed_preflight_fixture(tmp_path)
    for supplied in ([],evidence+evidence):
        with pytest.raises(ValueError):preflight(s,tid,'gate-needs',rev,draft,artifact_snapshot=supplied)
    with pytest.raises(ValueError):preflight(s,tid,'gate-needs',rev,'missing',artifact_snapshot=evidence)
    other=s.save_decision_draft(tid,'gate:gate-needs',rev,{'kind':'annotation','edited_text':'proposal'})
    with pytest.raises(ValueError):preflight(s,tid,'gate-needs',rev,other,artifact_snapshot=evidence)


def test_preflight_rejects_digest_of_unrelated_file_in_place_of_canonical_artifact(tmp_path):
    s,tid,rev,draft,evidence,preflight=_closed_preflight_fixture(tmp_path)
    with pytest.raises(ValueError,match='canonical'):
        preflight(s,tid,'gate-needs',rev,draft,artifact_snapshot=[evidence[0]|{'name':'notes.txt'}])


def test_preflight_rejects_task_change_during_snapshot_read(tmp_path,monkeypatch):
    s,tid,rev,draft,evidence,preflight=_closed_preflight_fixture(tmp_path)
    original=s.task;reads=[]
    def task(task_id):
        result=original(task_id);reads.append(True)
        if len(reads)>=3:result['generation']+=1
        return result
    monkeypatch.setattr(s,'task',task)
    with pytest.raises(ValueError):preflight(s,tid,'gate-needs',rev,draft,artifact_snapshot=evidence)
