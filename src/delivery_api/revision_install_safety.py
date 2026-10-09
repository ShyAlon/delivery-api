"""Read-only install safety classification. No install/retry/cleanup authority."""


def installation_safety_snapshot(*,parent_current,source_writer_excluded,unresolved_jobs,unresolved_attempts):
    if type(parent_current) is not bool or type(source_writer_excluded) is not bool:
        raise ValueError('exact current parent/writer evidence required')
    for values in (unresolved_jobs,unresolved_attempts):
        if not isinstance(values,list) or any(not isinstance(v,str) or not v or len(v)>200 for v in values):
            raise ValueError('exact unresolved work identities required')
    blockers=[]
    if not parent_current:blockers.append('parent_generation_or_session_changed')
    if not source_writer_excluded:blockers.append('source_writer_exclusion_unverified')
    if unresolved_jobs:blockers.append('worker_jobs_unresolved')
    if unresolved_attempts:blockers.append('provider_attempts_unresolved')
    # Empty blockers are observed safety evidence, never review/installation
    # permission. Destination exclusion and effect-chain checks remain separate.
    return {'kind':'closed_revision_install_safety_snapshot','blockers':blockers,
        'unresolved_job_ids':sorted(set(unresolved_jobs)),'unresolved_attempt_ids':sorted(set(unresolved_attempts)),
        'observed_work_quiescent':not blockers,'installation_available':False,'accepted':False,
        'installed':False,'dispatch_started':False,'automatic_replay_allowed':False}
