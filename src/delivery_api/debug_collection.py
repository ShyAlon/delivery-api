"""Bounded server-side evidence batch. No caller-selected SQL or routes."""
import time
from datetime import datetime,timezone


def collect(module,actor,task_id):
    from contextlib import nullcontext
    from .postgres_scope import request_stats
    sections=[];started=time.monotonic()
    reader=getattr(module.app.state,'operator_read_store',None) if actor.admin and module.config.CLOUD_TENANTS else None
    stages=[('credits',lambda:module.account_ledger_view(actor)),('credit-history',lambda:module.account_credit_history(actor)),('startup-checks',lambda:module.debug_preflight(actor))]
    if actor.admin:
        stages += [('request-timing',lambda:module.admin_operations_request_costs(actor)),('server-capacity',lambda:module.admin_operations_capacity(actor)),('log-durability',lambda:module.debug_log_durability(actor)),('durable-diagnostics',lambda:module.debug_evidence(task_id,actor))]
        if task_id:stages += [('db-task-state',lambda:module.operator_task_debug(task_id,actor)),('db-state-events',lambda:module.operator_task_logs(task_id,None,100,actor)),('db-start-failures',lambda:module.operator_task_failure_diagnostics(task_id,20,actor)),('db-usage',lambda:module.operator_task_usage(task_id,actor))]
    with reader.export_batch(module.store) if reader else nullcontext() as connection:
        for source,run in stages:
            began=time.monotonic();stats=request_stats.get();query_start=len(stats.statements) if stats else 0
            try:
                # One bad/missing source must not abort the other bounded sources.
                with connection.transaction() if connection else nullcontext():data=run()
                availability='unavailable' if data.get('availability') in ('missing','unavailable','unreadable') else 'available'
                row={'source':source,'availability':availability,'data':data}
            except Exception:row={'source':source,'availability':'unavailable','category':'read_failed','next':'Retry collection in Debug; missing evidence is not an empty result.'}
            row.update(duration_ms=round((time.monotonic()-began)*1000),collected_at=datetime.now(timezone.utc).isoformat(),sql_statements=len(stats.statements)-query_start if stats else None)
            sections.append(row)
    return {'sections':sections,'duration_ms':round((time.monotonic()-started)*1000),'scope':'fixed bounded evidence projections; per-source authority preserved'}
