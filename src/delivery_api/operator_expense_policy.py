"""Private operator task-generation cost ceiling. Customers see only credits."""
import uuid
from .preview_spend import limit_units,SCALE
from .attempt_ledger import AdmissionDenied


_POLICY_SQL='SELECT r.revision,r.limit_units,r.units_per_usd FROM public.operator_expense_policy_head h JOIN public.operator_expense_policy_revisions r USING(revision) WHERE h.singleton'


def configured(store):
    if store._connection.get() is not None:
        # Inside the dispatch transaction: same identity, same committed policy rows (READ COMMITTED), one statement instead of
        # a second connection with its own pool check, identity batch and commit (4 trips, 2026-11-10 trace).
        return store._execute_native(_POLICY_SQL).fetchone()
    with store._postgres.transaction() as conn:
        return conn.execute(_POLICY_SQL).fetchone()


def require(store,row):
    policy=configured(store)
    if not policy:raise AdmissionDenied('Operator expense policy missing; paid dispatch blocked')
    quote=row['quote'];job=store.worker_job(row['job_id'])
    if quote.get('expense_units_per_usd')!=policy['units_per_usd']:raise AdmissionDenied('Operator expense policy scale unverifiable')
    from .tenancy import current_scope
    attempts=store._execute_native("SELECT p.* FROM provider_attempts p JOIN worker_jobs j ON j.job_id=p.job_id WHERE p.owner_id=%s AND j.task_id=%s AND j.generation=%s AND p.quote->>'provider'='deepseek' AND p.state NOT IN ('cancelled','released')",(current_scope().owner_id,job['task_id'],job['generation'])).fetchall()
    total=0
    for item in attempts:
        q=item['quote']
        if q.get('expense_units_per_usd')!=SCALE:raise AdmissionDenied('Operator expense history scale unverifiable')
        if item['state']=='settled':amount=item['expense_actual']
        else:
            bound=(q.get('estimate_policy') or {}).get('ceiling',q['input_max'])
            rate=max(q.get('expense_cache_hit',q['expense_input']),q.get('expense_cache_miss',q['expense_input']))
            amount=bound*rate+q['output_max']*q['expense_output']
        if type(amount) is not int or amount<0:raise AdmissionDenied('Operator expense history incomplete')
        total+=amount
    if total>policy['limit_units']:raise AdmissionDenied('Operator task expense ceiling reached; no further paid dispatch')


def read(repository,store):
    authority=repository.principal.authorize(store,'services.operate')
    with repository.db.transaction() as conn:
        conn.execute('SET LOCAL ROLE requi_model_pin_editor')
        if not conn.execute('SELECT public.requi_operator_read_allowed(%s,%s) AS allowed',('services.operate',authority['registry_version'])).fetchone()['allowed']:raise PermissionError('Operator expense policy access denied')
        history=conn.execute('SELECT * FROM public.operator_expense_policy_revisions ORDER BY created_at DESC LIMIT 50').fetchall()
        head=conn.execute('SELECT revision FROM public.operator_expense_policy_head WHERE singleton').fetchone()
        return {'history':history,'current':next((r for r in history if head and r['revision']==head['revision']),None),'scope':'provider_usd_per_task_generation','activation':False}


def commit(repository,store,amount,expected,reason):
    units=limit_units(amount)
    if not 1<=len(reason.strip())<=400:raise ValueError('A reason of 1 to 400 characters is required')
    authority=repository.principal.authorize(store,'services.operate');revision=uuid.uuid4().hex
    with repository.db.transaction() as conn:
        conn.execute('SET LOCAL ROLE requi_model_pin_editor')
        if not conn.execute('SELECT public.requi_operator_read_allowed(%s,%s) AS allowed',('services.operate',authority['registry_version'])).fetchone()['allowed']:raise PermissionError('Operator expense policy write denied')
        conn.execute("SELECT pg_advisory_xact_lock(hashtextextended('operator-expense-policy',0))")
        current=conn.execute('SELECT revision FROM public.operator_expense_policy_head WHERE singleton').fetchone()
        if (current['revision'] if current else None)!=expected:raise ValueError('Policy changed; reload and review again')
        conn.execute('INSERT INTO public.operator_expense_policy_revisions(revision,limit_units,units_per_usd,operator_subject,reason,previous_revision) VALUES(%s,%s,%s,%s,%s,%s)',(revision,units,SCALE,authority['operator_subject'],reason.strip(),expected))
        conn.execute('INSERT INTO public.operator_expense_policy_head VALUES(true,%s) ON CONFLICT(singleton) DO UPDATE SET revision=excluded.revision',(revision,))
    return revision
