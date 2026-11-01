#!/usr/bin/env python3
"""Read-only catalog proof for the PG cutover schema and restricted role grants."""
import importlib.util
import re
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[2]
# Explicit pre-cutover baseline, not automatic discovery/grandfathering.
# New files must have reviewed probes below; coverage validation rejects omissions.
BASELINE_MIGRATIONS=frozenset({
 '20261007150000_tenant_control_plane.sql',
 '20261008111000_worker_execution_options.sql',
 '20261008122000_attempt_admission_ledger.sql',
 '20261008180000_private_admission_role.sql',
 '20261008181000_optional_admission_limits.sql',
 '20261008185000_attempt_settlement_timestamp.sql',
 '20261008190000_prepaid_credit_accounts.sql',
 '20261009064000_raw_provider_usage.sql',
 '20261009065500_cooperative_task_stop.sql',
 '20261009070000_private_expense_alert_outbox.sql',
 '20261009100000_srs_rework_job_options.sql',
 '20261009100500_srs_rework_gate_revision.sql',
 '20261009104000_operator_read_registry.sql',
 '20261009104500_operator_read_column_bounds.sql',
 '20261009111000_operator_debug_metadata.sql',
 '20261009112000_operator_state_event_log.sql',
 '20261009120000_operator_task_usage.sql',
 '20261009130000_private_credit_adjustment_evidence.sql',
 '20261009133000_task_operation_suspension.sql',
 '20261009150000_closed_revision_restart_intents.sql',
 '20261009152000_closed_revision_lineage_plans.sql',
 '20261009153000_closed_revision_journal.sql',
 '20261009162000_closed_revision_source_reviews.sql',
 '20261009170000_private_revision_installations.sql',
 '20261009173500_private_revision_acceptance.sql',
 '20261009175000_closed_revision_job_options.sql',
 '20261009213000_confirmed_expense_guard.sql',
 '20261010090000_private_payment_ledger.sql',
 '20261010093000_operator_payment_reads.sql',
 '20261010190000_exact_retail_contributions.sql',
 '20261011104000_private_unknown_compute_waiver.sql',
 '20261011183000_private_unknown_provider_failure.sql',
 '20261017100000_released_provider_attempts.sql',
 '20261020080000_credit_ledger.sql',
 '20261020130000_operator_stop_audit.sql',
 '20261020150000_operator_grant_credits.sql',
 '20261022090000_session_placement_leases.sql',
 '20261022120000_session_checkpoint_tables.sql',
 '20261022140000_artifact_manifest.sql',
 '20261022160000_staged_provider_responses.sql',
 '20261023220000_operator_attempt_release_audit.sql',
 '20261024090000_session_tables_least_privilege.sql',
 '20261025120000_operator_failure_diagnostics.sql',
})
MIGRATIONS={

 '20261029203000_resource_metrics.sql':('table:resource_run_samples','table:resource_run_summaries','table:resource_container_samples','table:resource_container_summaries'),
 '20261029210000_generator_snapshots.sql':('table:generator_snapshots',),
 '20261029220000_generator_documents.sql':('table:generator_documents',),
 '20261030010000_operator_log_records.sql':('table:operator_log_records','table:operator_startup_reports'),
 '20261030020000_resource_metrics_bounds.sql':('constraint:resource_run_samples_finite','constraint:resource_container_samples_finite'),
 '20261030030000_resource_metrics_maintenance.sql':('function:requi_maintain_resource_metrics',),
 '20261030040000_engine_quality_config.sql':('table:engine_quality_config',),
 '20261030050000_operator_usage_default.sql':('function0:requi_operator_usage_default','trigger:operator_usage_default'),
 '20261030223000_own_test_expense_profile.sql':('table:own_test_expense_profiles','signature:requi_own_test_expense_guard(text,bigint)'),
 '20261031044500_final_packages.sql':('table:final_packages',),
 '20261031163000_admission_totals.sql':('table:admission_totals','table:admission_total_entries','signature:requi_admission_totals(double precision)','signature:requi_admission_project(public.provider_attempts)','signature:requi_admission_changed()'),
 '20261031180000_expense_totals.sql':('table:expense_totals','table:expense_total_entries','table:expense_total_clock','signature:requi_refresh_expense_totals(double precision)'),
 '20261031190000_retail_totals.sql':('table:retail_totals','signature:requi_retail_total_changed()'),
 '20261031200000_waiting_input_index.sql':('table:events_waiting_input_identity',),
 '20261031210000_document_chunks.sql':('table:document_chunks','signature:requi_document_parts(text,integer,text)','signature:requi_document_append(text,integer,text,bytea,uuid,uuid,double precision)'),
 '20261031220000_usage_projection.sql':('table:document_projections',),
 '20261101073000_fenced_job_force_close.sql':('signature:requi_force_close_fenced_job(text,text,text)',),
 '20261101124000_visible_task_names.sql':('visible-title-index:idx_tasks_tenant_title',),
 '20261101150000_preview_spend_approvals.sql':('table:preview_spend_approvals',),
 '20261101160000_model_pins.sql':('table:model_pin_revisions','table:model_pin_heads','table:operator_expense_policy_revisions','table:operator_expense_policy_head'),
}
ROLES=('requi_metrics_writer','requi_engine_config_read','requi_operator_usage','requi_model_pin_editor')

def destructive(text):
    # Keep strings/function bodies, including dynamic SQL, but strip comments.
    # GRANT DELETE and policy FOR DELETE are privileges, not data mutation.
    text=re.sub(r'/\*.*?\*/|--[^\n]*',' ',text,flags=re.S)
    found=[]
    for word,pattern in [('DROP',r'\bDROP\s+(?:TABLE|SCHEMA|FUNCTION|ROLE|POLICY|INDEX|TYPE|VIEW|SEQUENCE|CONSTRAINT|COLUMN)\b'),('DELETE',r'\bDELETE\s+FROM\b'),('TRUNCATE',r'\bTRUNCATE\b')]:
        if re.search(pattern,text,re.I):found.append(word)
    return found

def object_exists(conn,probe):
    kind,name=probe.split(':',1)
    if kind=='visible-title-index':
        row=conn.execute("SELECT i.indisunique,pg_get_expr(i.indpred,i.indrelid) FROM pg_index i JOIN pg_class c ON c.oid=i.indexrelid WHERE c.relnamespace='public'::regnamespace AND c.relname=%s",(name,)).fetchone()
        return bool(row and row[0] and row[1] and 'deleted_at IS NULL' in row[1])
    if kind=='signature':return conn.execute('SELECT to_regprocedure(%s) IS NOT NULL',('public.'+name,)).fetchone()[0]
    if kind=='table':return conn.execute('SELECT to_regclass(%s) IS NOT NULL',('public.'+name,)).fetchone()[0]
    if kind=='function0':return conn.execute('SELECT to_regprocedure(%s) IS NOT NULL',('public.'+name+'()',)).fetchone()[0]
    if kind=='trigger':return conn.execute("SELECT EXISTS(SELECT 1 FROM pg_trigger WHERE tgrelid='public.operator_principals'::regclass AND tgname=%s AND NOT tgisinternal)",(name,)).fetchone()[0]
    if kind=='constraint':return conn.execute("SELECT EXISTS(SELECT 1 FROM pg_constraint c JOIN pg_namespace n ON n.oid=c.connamespace WHERE n.nspname='public' AND c.conname=%s)",(name,)).fetchone()[0]
    return conn.execute('SELECT to_regprocedure(%s) IS NOT NULL',('public.'+name+'(double precision,double precision)',)).fetchone()[0]

def validate_coverage(root=ROOT):
    disk={p.name for p in (root/'supabase/migrations').glob('*.sql')}
    covered=BASELINE_MIGRATIONS | set(MIGRATIONS)
    if BASELINE_MIGRATIONS & set(MIGRATIONS) or covered!=disk:
        raise ValueError('migration inventory coverage mismatch; add reviewed catalog probes for new files: '+','.join(sorted(disk-covered))+'; missing files: '+','.join(sorted(covered-disk)))

def inventory(conn):
    validate_coverage()
    rows=[]
    for name,probes in MIGRATIONS.items():
        exists=[bool(object_exists(conn,p)) for p in probes]
        rows.append({'file':name,'status':'present' if all(exists) else 'partial' if any(exists) else 'pending',
                     'destructive':destructive((ROOT/'supabase/migrations'/name).read_text())})
    return rows

def grants(conn):
    result=[]
    for role in ROLES:
        exists=conn.execute('SELECT EXISTS(SELECT 1 FROM pg_roles WHERE rolname=%s)',(role,)).fetchone()[0]
        can=exists and conn.execute('SELECT pg_has_role(current_user,%s,\'SET\')',(role,)).fetchone()[0]
        if not can:result.append(role)
    return result

def settings():
    spec=importlib.util.spec_from_file_location('requi_settings',ROOT/'scripts/ops/settings.py')
    m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m

def ready(rows,missing):
    return not missing and all(r['status']=='present' for r in rows if r['file']!='20261030010000_operator_log_records.sql')

def review_and_recheck(check,offer):
    status=check()
    if status!=1:return status  # Unavailable/error is not pending-schema evidence.
    result=offer()
    if result:return result
    return check()

def main():
    import psycopg
    s=settings();values=s.load(ROOT)
    if not values.get('DELIVERY_DATABASE_URL'):raise ValueError('DELIVERY_DATABASE_URL missing from protected settings')
    with psycopg.connect(values['DELIVERY_DATABASE_URL'],connect_timeout=10,autocommit=True) as conn:
        conn.execute('SET default_transaction_read_only=on')
        rows=inventory(conn);missing=grants(conn)
    for row in rows:print(row['status'].upper(),row['file'],('REVIEW REQUIRED '+','.join(row['destructive'])) if row['status']!='present' and row['destructive'] else '')
    for role in missing:print('MISSING SET membership:',role)
    if not ready(rows,missing):
        print('SCHEMA NOT READY: run python3 scripts/owner/apply-pg-cutover.py . for a dry review. No database changes made.');return 1
    print('SCHEMA READY: cutover catalog objects and app SET memberships present (not a full migration checksum proof).');return 0

if __name__=='__main__':
    try:
        if '--offer-apply' in sys.argv:
            import subprocess
            def offer():
                return subprocess.call([sys.executable,str(ROOT/'scripts/owner/apply-pg-cutover.py'),str(ROOT),'--apply','--review-projection-functions','--preview-offer'])
            sys.exit(review_and_recheck(main,offer))
        sys.exit(main())
    except Exception as exc:
        print('SCHEMA CHECK FAILED: '+type(exc).__name__+'; no values printed; no database changes.',file=sys.stderr);sys.exit(2)
