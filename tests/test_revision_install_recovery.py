import pytest
from delivery_api.revision_install_recovery import classify_installation_recovery


@pytest.mark.parametrize('journal,stage,files,db,state',[
    ('installed',False,True,True,'installed_verified_no_dispatch'),
    ('install_pending',False,True,False,'files_promoted_database_uninstalled_requires_review'),
    ('promotion_pending',True,False,False,'private_stage_requires_review'),
    ('installed',False,False,True,'database_installed_requires_review'),
    ('promotion_pending',True,True,False,'duplicate_namespaces_require_review'),
    ('authorized',False,False,False,'installation_evidence_unverified_requires_review')])
def test_all_crash_states_forbid_automatic_effects(journal,stage,files,db,state):
    result=classify_installation_recovery(journal_state=journal,private_stage_present=stage,promoted_files_verified=files,database_installed=db)
    assert result['state']==state and not result['automatic_retry'] and not result['automatic_cleanup'] and not result['resume_available']


def test_observed_presence_is_not_optional_approval():
    with pytest.raises(ValueError):classify_installation_recovery(journal_state='installed',private_stage_present=None,promoted_files_verified=True,database_installed=True)
