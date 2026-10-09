import hashlib,json
from pathlib import Path
import pytest
from delivery_api.revision_install_review import installation_review_payload
from delivery_api.revision_install_plan import prepare_installation_plan
from delivery_api.revision_install_files import prepare_execution_anchor,promote_private_stage
from test_revision_install_review import args


def setup(tmp_path):
    a=args();lineage=a['lineage'];lineage['owner_id']='00000000-0000-0000-0000-000000000001';lineage['proposal_id']='a'*32
    a['lineage_sha256']=hashlib.sha256(json.dumps(lineage,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    review=installation_review_payload(**a);plan=prepare_installation_plan(**review,installation_id='test-install')
    sessions=tmp_path/'tenants'/lineage['owner_id']/'sessions'
    stage=sessions/lineage['parent_execution_session_id']/'spec'/'revision-proposals'/lineage['proposal_id']/'staged'/lineage['execution_session_id']
    (stage/'interview').mkdir(parents=True);(stage/'spec').mkdir();(stage/'spec'/'test').write_text('preserved')
    return stage,lineage,plan,sessions/lineage['execution_session_id']


def test_private_anchor_and_no_overwrite_promotion_are_not_db_install(tmp_path):
    stage,lineage,plan,destination=setup(tmp_path);checks=[]
    kw=plan|{'validate_authority':lambda:checks.append(1)}
    receipt=prepare_execution_anchor(stage_root=stage,**kw)
    assert not receipt['installed'] and not (stage/'interview'/'stop-request.json').exists()
    promoted=promote_private_stage(artifacts_base=tmp_path,lineage=lineage,**kw)
    assert promoted['files_promoted'] and not promoted['installed'] and not promoted['dispatch_started']
    assert not stage.exists() and (destination/'spec'/'test').read_text()=='preserved' and len(checks)>=6


def test_anchor_existing_or_foreign_authority_refuses_write(tmp_path):
    stage,lineage,plan,destination=setup(tmp_path)
    def revoked():raise PermissionError('review revoked')
    with pytest.raises(PermissionError):prepare_execution_anchor(stage_root=stage,**plan,validate_authority=revoked)
    assert not (stage/'execution-generation.json').exists()
    prepare_execution_anchor(stage_root=stage,**plan,validate_authority=lambda:None)
    with pytest.raises(ValueError,match='already present'):prepare_execution_anchor(stage_root=stage,**plan,validate_authority=lambda:None)


def test_promotion_never_overwrites_existing_destination(tmp_path):
    stage,lineage,plan,destination=setup(tmp_path)
    prepare_execution_anchor(stage_root=stage,**plan,validate_authority=lambda:None)
    destination.mkdir();(destination/'keep').write_text('original')
    with pytest.raises(ValueError,match='destination exists'):
        promote_private_stage(artifacts_base=tmp_path,lineage=lineage,**plan,validate_authority=lambda:None)
    assert (destination/'keep').read_text()=='original' and stage.exists()


def test_stop_copy_and_symlink_stage_rejected(tmp_path):
    stage,lineage,plan,destination=setup(tmp_path)
    (stage/'interview'/'stop-request.json').write_text('{}')
    with pytest.raises(ValueError,match='Stop marker'):prepare_execution_anchor(stage_root=stage,**plan,validate_authority=lambda:None)
    (stage/'interview'/'stop-request.json').unlink()
    other=stage.with_name('other');stage.rename(other);stage.symlink_to(other,target_is_directory=True)
    with pytest.raises((ValueError,PermissionError,OSError)):prepare_execution_anchor(stage_root=stage,**plan,validate_authority=lambda:None)
