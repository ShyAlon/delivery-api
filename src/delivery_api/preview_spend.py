"""Task-generation dollar approval, independent of route visibility and credits.

The existing global/owner dispatch locks serialize guard reads and transitions.
Unknown outcomes retain their entire worst-case hold; no automatic top-up/retry.
"""
from decimal import Decimal, InvalidOperation
import time
from .attempt_ledger import AdmissionDenied
from .tenancy import current_scope
SCALE=10**12


def limit_units(value):
    try:
        amount=Decimal(value)
        units=amount*SCALE
        if not amount.is_finite() or amount<=0 or units!=units.to_integral_value() or units>2**63-1:raise ValueError()
        return int(units)
    except (ValueError,TypeError,InvalidOperation):raise ValueError('Enter a positive USD ceiling with at most 12 decimal places') from None


def approve(store,task_id,model,amount):
    from .prepaid import owner_lock
    owner_lock(store)
    from .suspension_policy import require_task_operation
    require_task_operation(store,'task.start')
    scope=current_scope();task=store.task(task_id)
    if not task or task['state']!='not_started':raise ValueError('Approve only a not-started task; running approvals cannot be changed')
    units=limit_units(amount)
    existing=store._execute_native('SELECT * FROM preview_spend_approvals WHERE owner_id=%s AND task_id=%s AND generation=%s',
        (scope.owner_id,task_id,task['generation'])).fetchone()
    if existing:
        if (existing['model'],existing['limit_units'],existing['units_per_usd'],str(existing['auth_session_id']))!=(model,units,SCALE,scope.session_id):
            raise ValueError('An immutable approval already exists for this task generation; create a new task for a different ceiling')
        return
    store._execute_native('INSERT INTO preview_spend_approvals VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)',
        (scope.owner_id,task_id,task['generation'],scope.session_id,'deepseek',model,units,SCALE,time.time()),write=True)
    store.audit(scope.owner_id,'preview.spend.approved',task_id,{'provider':'deepseek','model':model,'generation':task['generation'],'limit_units':units,'units_per_usd':SCALE})


def require(store,row):
    job=store.worker_job(row['job_id']);scope=current_scope()
    approval=store._execute_native('SELECT * FROM preview_spend_approvals WHERE owner_id=%s AND task_id=%s AND generation=%s',
        (scope.owner_id,job['task_id'],job['generation'])).fetchone()
    quote=row['quote']
    if not approval:raise AdmissionDenied('Paid preview requires explicit task USD ceiling confirmation')
    if (str(approval['auth_session_id']),approval['provider'],approval['model'],approval['units_per_usd'])!=(scope.session_id,quote['provider'],quote['model'],quote.get('expense_units_per_usd')):
        raise AdmissionDenied('Paid preview approval session/model/expense scale differs')
    attempts=store._execute_native("SELECT p.* FROM provider_attempts p JOIN worker_jobs j ON j.job_id=p.job_id WHERE p.owner_id=%s AND j.task_id=%s AND j.generation=%s AND p.quote->>'provider'='deepseek' AND p.state NOT IN ('cancelled','released')",
        (scope.owner_id,job['task_id'],job['generation'])).fetchall()
    total=0
    for item in attempts:
        if item['quote'].get('expense_units_per_usd')!=approval['units_per_usd']:raise AdmissionDenied('Paid preview expense history scale unverifiable')
        if item['state']=='settled':amount=item['expense_actual']
        else:
            q=item['quote'];policy=q.get('estimate_policy')
            # Paid quote input_max is only a wire-byte estimate. Reserve the
            # full reviewed context window, never pretend estimate is a bound.
            bound=policy['ceiling'] if policy else q['input_max']
            rate=max(q.get('expense_cache_hit',q['expense_input']),q.get('expense_cache_miss',q['expense_input']))
            amount=bound*rate+q['output_max']*q['expense_output']
        if type(amount) is not int or amount<0:raise AdmissionDenied('Paid preview expense history incomplete')
        total+=amount
    if total>approval['limit_units']:raise AdmissionDenied('Paid preview USD ceiling reached; no further paid dispatch')
