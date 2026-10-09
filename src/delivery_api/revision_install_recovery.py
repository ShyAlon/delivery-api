"""Install crash classification, never resumes/promotes/cleans automatically."""


def classify_installation_recovery(*,journal_state,private_stage_present,promoted_files_verified,database_installed):
    states=('authorized','promotion_pending','files_promoted','install_pending','installed','held_for_review')
    if journal_state not in states or any(type(v) is not bool for v in (private_stage_present,promoted_files_verified,database_installed)):
        raise ValueError('exact installation reconciliation observations required')
    if database_installed and promoted_files_verified and journal_state=='installed' and not private_stage_present:
        state='installed_verified_no_dispatch'
    elif database_installed:
        state='database_installed_requires_review'
    elif private_stage_present and promoted_files_verified:
        state='duplicate_namespaces_require_review'
    elif promoted_files_verified:
        state='files_promoted_database_uninstalled_requires_review'
    elif private_stage_present:
        state='private_stage_requires_review'
    else:state='installation_evidence_unverified_requires_review'
    return {'kind':'closed_revision_installation_reconciliation','state':state,'journal_state':journal_state,
        'automatic_retry':False,'automatic_cleanup':False,'automatic_dispatch':False,'resume_available':False}
