"""What a compute-accounting segment means for the next provider call. Pure rules, no database: the ledger, the Debug view and the release action all use them."""


def compute_block(state,job_state):
    """Why a segment stops new provider calls for its owner, or None. Mirrors the admission check in attempt_ledger.

    An unresolved segment always blocks. A pending one blocks once its job is no longer running (an orphan)."""
    if state=='unresolved':return 'unresolved'
    if state=='pending' and job_state is not None and job_state!='running':return 'pending, job not running'
    return None


def close_decision(provider_attempts,token_rows):
    """'void' when the job never reached a provider and posted no token charge, so there is no compute to price; otherwise 'hold' for review."""
    return 'void' if int(provider_attempts)==0 and int(token_rows)==0 else 'hold'


def releasable(state,job_state,provider_attempts,token_rows):
    """A segment can be released by its owner only if it is not settled, its job is not running, and the job never reached a provider."""
    return state!='settled' and job_state!='running' and close_decision(provider_attempts,token_rows)=='void'


def segment_view(row,provider_attempts,token_rows):
    """Fixed, non-sensitive fields for one segment, as shown in the Debug ledger."""
    state,job_state=row['state'],row.get('job_state')
    return {'key':str(row['segment_key'])[:8],'job':str(row['job_id'])[:8],'phase':row.get('phase_key'),'state':state,'job_state':job_state,
            'provider_calls':int(provider_attempts),'blocks_admission':compute_block(state,job_state) is not None,
            'releasable':releasable(state,job_state,provider_attempts,token_rows)}
