"""Narrow cross-user read path under an explicit RLS operator-read role."""
from .tenancy import TenantAccessDenied
from contextlib import contextmanager

class OperatorReadStore:
    def __init__(self,dsn,principal):
        from .postgres_scope import ScopedPostgres
        self.db=ScopedPostgres(dsn);self.principal=principal

    @contextmanager
    def _read(self,owner_store,capability,*,role="requi_operator_read"):
        authority=self.principal.authorize(owner_store,capability)
        with self.db.transaction(repeatable_read=role in ('requi_operator_usage','requi_operator_payment_read')) as connection:
            if role not in ('requi_operator_read','requi_operator_usage','requi_operator_payment_read'):raise ValueError('fixed role required')
            connection.execute('SET LOCAL ROLE '+role)
            allowed=connection.execute('SELECT public.requi_operator_read_allowed(%s,%s) AS allowed',
                (capability,authority['registry_version'])).fetchone()
            if not allowed or allowed['allowed'] is not True:raise TenantAccessDenied('database operator registry denied')
            yield connection,authority

    def sessions(self,owner_store,*,limit=100,before=None):
        if type(limit) is not int or not 1<=limit<=100:raise ValueError('bounded operator page required')
        if before is not None:
            import math
            if (not isinstance(before,dict) or set(before)!={'created_at','session_id'}
                or type(before['created_at']) not in (int,float) or not math.isfinite(before['created_at'])
                or before['created_at']<0 or not isinstance(before['session_id'],str)
                or not 1<=len(before['session_id'])<=240):raise ValueError('exact bounded session page cursor required')
        with self._read(owner_store,'sessions.read') as (connection,authority):
            if before is None:
                rows=connection.execute('SELECT session_id,owner_id,task_id,generation,created_at FROM public.engine_sessions ORDER BY created_at DESC,session_id DESC LIMIT %s',(limit+1,)).fetchall()
            else:
                rows=connection.execute('SELECT session_id,owner_id,task_id,generation,created_at FROM public.engine_sessions WHERE (created_at,session_id)<(%s,%s) ORDER BY created_at DESC,session_id DESC LIMIT %s',
                    (before['created_at'],before['session_id'],limit+1)).fetchall()
            truncated=len(rows)>limit;page=rows[:limit]
            return {'scope':'reviewed_cross_user_operator_read','operator_subject':authority['operator_subject'],
                'registry_version':authority['registry_version'],'sessions':[{**row,'owner_id':str(row['owner_id'])} for row in page],
                'limit':limit,'truncated':truncated,
                'next_before':{'created_at':page[-1]['created_at'],'session_id':page[-1]['session_id']} if truncated else None,
                'mutations_available':False}

    def credits(self,owner_store,*,limit=100,after_owner=None):
        """Fixed-point account page, no inferred unit conversion or adjustments."""
        from uuid import UUID
        if type(limit) is not int or not 1<=limit<=100:raise ValueError('bounded operator page required')
        if after_owner is not None:
            try:
                if str(UUID(after_owner))!=after_owner:raise ValueError()
            except (ValueError,TypeError,AttributeError) as exc:raise ValueError('canonical account page cursor required') from exc
        with self._read(owner_store,'analytics.read') as (connection,authority):
            if after_owner is None:
                rows=connection.execute('SELECT owner_id,balance_units,units_per_credit,updated_at FROM public.credit_accounts ORDER BY owner_id LIMIT %s',(limit+1,)).fetchall()
            else:
                rows=connection.execute('SELECT owner_id,balance_units,units_per_credit,updated_at FROM public.credit_accounts WHERE owner_id>%s::uuid ORDER BY owner_id LIMIT %s',(after_owner,limit+1)).fetchall()
            truncated=len(rows)>limit;page=rows[:limit]
            return {'scope':'reviewed_cross_user_operator_read','operator_subject':authority['operator_subject'],
                'registry_version':authority['registry_version'],'accounts':[{**row,'owner_id':str(row['owner_id'])} for row in page],
                'truncated':truncated,'next_owner':str(page[-1]['owner_id']) if truncated else None,
                'credit_adjustment_available':False}

    def expense_totals(self,owner_store):
        """Atomic database-as-of totals, not installed admission or SMTP policy.

        Held expense never ages out. No prices, prompts or raw usage returned.
        Account-count-derived platform limit is deliberately absent until a
        complete registered/non-deleted account registry exists.
        """
        with self._read(owner_store,'analytics.read') as (connection,authority):
            row=connection.execute("""
              WITH moment AS (SELECT extract(epoch FROM statement_timestamp())::double precision AS as_of)
              SELECT moment.as_of,
                COALESCE(sum(p.expense_actual) FILTER (WHERE p.state='settled' AND p.settled_at>moment.as_of-86400 AND p.settled_at<=moment.as_of),0) AS rolling_settled_units,
                COALESCE(sum(p.expense_reserved) FILTER (WHERE p.state IN ('reserved','dispatched','uncertain','failure_waived')),0) AS outstanding_hold_units,
                count(p.attempt_id) FILTER (WHERE p.state='uncertain') AS uncertain_attempts,
                count(p.attempt_id) FILTER (WHERE p.state='settled' AND (p.settled_at IS NULL OR p.expense_actual IS NULL OR p.settled_at>moment.as_of)) AS unverifiable_settlements
              FROM moment LEFT JOIN public.provider_attempts p ON true GROUP BY moment.as_of
            """).fetchone()
            if row['unverifiable_settlements']:
                raise TenantAccessDenied('operator expense settlement evidence incomplete')
            return {'scope':'reviewed_cross_user_operator_read','operator_subject':authority['operator_subject'],
                'registry_version':authority['registry_version'],'as_of':row['as_of'],
                'rolling_settled_units':int(row['rolling_settled_units']),
                'outstanding_hold_units':int(row['outstanding_hold_units']),
                'conservative_used_units':int(row['rolling_settled_units']+row['outstanding_hold_units']),
                'uncertain_attempts':row['uncertain_attempts'],'units':'configured expense fixed-point units',
                'platform_limit_available':False,'installed_in_admission':False}

    def task_debug(self,owner_store,task_id):
        """Bounded state metadata, never raw logs, prompts, errors or file paths."""
        if not isinstance(task_id,str) or not 1<=len(task_id)<=240:
            raise ValueError('bounded task identifier required')
        with self._read(owner_store,'debug.read') as (connection,authority):
            task=connection.execute('SELECT task_id,owner_id,state,active_phase,generation,created_at,updated_at,deleted_at FROM public.tasks WHERE task_id=%s',(task_id,)).fetchone()
            if task is None:raise KeyError('task metadata unavailable')
            jobs=connection.execute('SELECT job_id,owner_id,task_id,session_id,generation,kind,state,created_at,updated_at FROM public.worker_jobs WHERE task_id=%s ORDER BY created_at DESC,job_id DESC LIMIT 101',(task_id,)).fetchall()
            return {'scope':'reviewed_cross_user_operator_read','operator_subject':authority['operator_subject'],
                'registry_version':authority['registry_version'],'task':{**task,'owner_id':str(task['owner_id'])},
                'worker_jobs':[{**row,'owner_id':str(row['owner_id'])} for row in jobs[:100]],
                'worker_jobs_truncated':len(jobs)>100,'content':'state_metadata_only',
                'logs_available':False,'quiescence_verified':False,'mutations_available':False}

    def task_logs(self,owner_store,task_id,*,before_id=None,limit=100):
        """Curated state events only, collected after migration, not historical logs."""
        if not isinstance(task_id,str) or not 1<=len(task_id)<=240:raise ValueError('bounded task identifier required')
        if type(limit) is not int or not 1<=limit<=100:raise ValueError('bounded event page required')
        if before_id is not None and (type(before_id) is not int or not 1<=before_id<=9223372036854775807):
            raise ValueError('positive bounded event cursor required')
        with self._read(owner_store,'logs.read') as (connection,authority):
            query='SELECT id,owner_id,task_id,source,event,state,generation,ts FROM public.operator_state_events WHERE task_id=%s'
            params=[task_id]
            if before_id is not None:query+=' AND id<%s';params.append(before_id)
            rows=connection.execute(query+' ORDER BY id DESC LIMIT %s',(*params,limit+1)).fetchall()
            page=rows[:limit];truncated=len(rows)>limit
            return {'scope':'reviewed_cross_user_operator_read','operator_subject':authority['operator_subject'],
                'registry_version':authority['registry_version'],'events':[{**row,'id':str(row['id']),'owner_id':str(row['owner_id'])} for row in page],
                'truncated':truncated,'next_before_id':str(page[-1]['id']) if truncated else None,
                'content':'curated_state_events_only','historical_backfill':False,'raw_logs_available':False,
                'quiescence_verified':False,'mutations_available':False}

    def credit_account(self,owner_store,owner_id):
        from uuid import UUID
        try:
            if str(UUID(owner_id))!=owner_id:raise ValueError()
        except (ValueError,TypeError,AttributeError) as exc:raise ValueError('canonical account required') from exc
        with self._read(owner_store,'analytics.read') as (connection,authority):
            row=connection.execute('SELECT owner_id,balance_units,units_per_credit,updated_at FROM public.credit_accounts WHERE owner_id=%s::uuid',(owner_id,)).fetchone()
            if row is None:raise KeyError('account unavailable')
            return {'scope':'reviewed_cross_user_operator_read','account':{**row,'owner_id':str(row['owner_id'])},'credit_adjustment_available':False}

    def task_usage(self,owner_store,task_id):
        if not isinstance(task_id,str) or not 1<=len(task_id)<=240:raise ValueError('bounded task required')
        with self._read(owner_store,'usage.read',role='requi_operator_usage') as (connection,authority):
            task=connection.execute('SELECT task_id,owner_id FROM public.tasks WHERE task_id=%s',(task_id,)).fetchone()
            if task is None:raise KeyError('task usage unavailable')
            row=connection.execute("""
              WITH attempts AS (SELECT p.attempt_id,p.owner_id,p.state,p.usage FROM public.provider_attempts p JOIN public.worker_jobs j ON j.job_id=p.job_id AND j.owner_id=p.owner_id WHERE j.task_id=%s),
              evidence AS (SELECT *, state='settled' AND jsonb_typeof(usage->'input_tokens')='number' AND jsonb_typeof(usage->'output_tokens')='number'
                AND (usage->>'input_tokens') ~ '^[0-9]+$' AND (usage->>'output_tokens') ~ '^[0-9]+$' AS verified FROM attempts)
              SELECT count(*) AS attempts, count(*) FILTER(WHERE state IN ('reserved','dispatched','uncertain')) AS unresolved_attempts,
                count(*) FILTER(WHERE state='settled' AND verified IS NOT TRUE) AS unknown_settled_usage,
                count(*) FILTER(WHERE state='failure_waived') AS waived_unknown_attempts,
                COALESCE(sum(CASE WHEN verified THEN (usage->>'input_tokens')::numeric ELSE 0 END),0) AS input_tokens,
                COALESCE(sum(CASE WHEN verified THEN (usage->>'output_tokens')::numeric ELSE 0 END),0) AS output_tokens,
                count(*) FILTER(WHERE state='settled' AND NOT EXISTS(SELECT 1 FROM public.credit_charges c WHERE c.attempt_id=evidence.attempt_id AND c.owner_id=evidence.owner_id)) AS settled_without_charge
              FROM evidence
            """,(task_id,)).fetchone()
            charges=connection.execute("""SELECT c.state,c.units_per_credit,
              count(*) AS charges, count(*) FILTER(WHERE c.charged_units IS NULL OR c.charged_units<0) AS invalid_amounts,
              COALESCE(sum(c.charged_units),0) AS charged_units
              FROM public.credit_charges c JOIN public.provider_attempts p ON p.attempt_id=c.attempt_id AND p.owner_id=c.owner_id
              JOIN public.worker_jobs j ON j.job_id=p.job_id AND j.owner_id=p.owner_id WHERE j.task_id=%s
              GROUP BY c.state,c.units_per_credit ORDER BY c.state,c.units_per_credit""",(task_id,)).fetchall()
            groups=[];unknown=pending=0
            for charge in charges:
                if charge['state']=='failure_waived':continue
                if charge['state']=='pending':pending+=charge['charges']
                elif charge['units_per_credit'] is None or charge['invalid_amounts']:unknown+=charge['charges']
                else:groups.append({'charged_units':str(charge['charged_units']),'units_per_credit':str(charge['units_per_credit']),'charges':charge['charges']})
            return {'scope':'reviewed_cross_user_operator_read','task_id':task_id,'owner_id':str(task['owner_id']),
                'input_tokens':str(row['input_tokens']),'output_tokens':str(row['output_tokens']),
                'total_tokens':str(row['input_tokens']+row['output_tokens']),'attempts':row['attempts'],
                'unresolved_attempts':row['unresolved_attempts'],'unknown_settled_usage':row['unknown_settled_usage'],
                'waived_unknown_attempts':row['waived_unknown_attempts'],
                'settled_credit_groups':groups,'unknown_charge_scale_or_amount':unknown,'pending_charges':pending,
                'settled_attempts_without_charge':row['settled_without_charge'],
                'complete':not (row['waived_unknown_attempts'] or row['unresolved_attempts'] or row['unknown_settled_usage'] or unknown or pending or row['settled_without_charge']),
                'source':'normalized settled attempt usage and actual prepaid charges','mutations_available':False}

    def payment_journal(self,owner_store,*,start_at,end_at,owner_id=None,limit=100,before_created_at=None,before_purchase_id=None):
        from .payment_reporting import validate_period
        validate_period(start_at,end_at,owner_id)
        if type(limit) is not int or not 1<=limit<=100:raise ValueError('bounded payment page required')
        if (before_created_at is None)!=(before_purchase_id is None):raise ValueError('complete payment page cursor required')
        if before_created_at is not None:
            import math
            if type(before_created_at) not in (int,float) or not math.isfinite(before_created_at) or before_created_at<0 or not isinstance(before_purchase_id,str) or not 1<=len(before_purchase_id)<=127:raise ValueError('bounded payment page cursor required')
        query='SELECT p.purchase_id,p.owner_id,p.processor,p.merchant_account,p.live_mode,p.offer_version,p.offer_id,p.currency,p.amount_minor,p.credit_units,p.units_per_credit,p.state,p.credit_posted,p.created_at,p.updated_at,c.processor_fee_minor,c.net_received_minor FROM public.payment_purchases p LEFT JOIN public.payment_credits c ON c.purchase_id=p.purchase_id WHERE p.created_at>=%s AND p.created_at<%s'
        params=[start_at,end_at]
        if owner_id:query+=' AND p.owner_id=%s';params.append(owner_id)
        if before_created_at is not None:query+=' AND (p.created_at,p.purchase_id)<(%s,%s)';params.extend([before_created_at,before_purchase_id])
        query+=' ORDER BY p.created_at DESC,p.purchase_id DESC LIMIT %s';params.append(limit+1)
        with self._read(owner_store,'payments.read',role='requi_operator_payment_read') as (connection,authority):
            rows=connection.execute(query,params).fetchall()
        truncated=len(rows)>limit;page=rows[:limit]
        for row in page:
            row['owner_id']=str(row['owner_id'])
            for key in ('amount_minor','credit_units','units_per_credit','processor_fee_minor','net_received_minor'):
                if row[key] is not None:row[key]=str(row[key])
            row['receiving_cost_known']=row['processor_fee_minor'] is not None
        return {'scope':'reviewed_cross_user_operator_read','payments':page,'limit':limit,'truncated':truncated,
            'next_before':{'created_at':page[-1]['created_at'],'purchase_id':page[-1]['purchase_id']} if truncated else None,
            'start_at':start_at,'end_at':end_at,'mutations_available':False,'profit_available':False}

    def payment_totals(self,owner_store,*,start_at,end_at,owner_id=None,limit=100):
        from .payment_reporting import validate_period,cost_caveats
        validate_period(start_at,end_at,owner_id)
        if type(limit) is not int or not 1<=limit<=100:raise ValueError('bounded payment total group page required')
        query="""SELECT c.processor,c.merchant_account,c.live_mode,c.owner_id,p.offer_version,p.offer_id,c.currency,
          count(*) AS purchases,sum(c.amount_minor)::text AS gross_minor,
          COALESCE(sum(c.processor_fee_minor),0)::text AS known_fee_minor,
          COALESCE(sum(c.net_received_minor),0)::text AS known_net_minor,
          count(*) FILTER(WHERE c.processor_fee_minor IS NULL) AS unknown_receiving_cost_count,
          count(*) FILTER(WHERE p.state='operator_review') AS operator_review_count
          FROM public.payment_credits c JOIN public.payment_purchases p USING(purchase_id)
          WHERE c.posted_at>=%s AND c.posted_at<%s"""
        params=[start_at,end_at]
        if owner_id:query+=' AND c.owner_id=%s';params.append(owner_id)
        query+=' GROUP BY c.processor,c.merchant_account,c.live_mode,c.owner_id,p.offer_version,p.offer_id,c.currency ORDER BY c.owner_id,p.offer_id,c.processor,c.merchant_account,c.live_mode,p.offer_version,c.currency LIMIT %s';params.append(limit+1)
        with self._read(owner_store,'payments.read',role='requi_operator_payment_read') as (connection,authority):
            rows=connection.execute(query,params).fetchall()
        for row in rows:row['owner_id']=str(row['owner_id'])
        return {'scope':'reviewed_cross_user_operator_read','groups':rows[:limit],'truncated':len(rows)>limit,
            'limit':limit,'start_at':start_at,'end_at':end_at,'mutations_available':False,**cost_caveats()}
