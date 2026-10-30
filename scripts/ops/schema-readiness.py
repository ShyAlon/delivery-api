#!/usr/bin/env python3
"""Read-only catalog proof for the PG cutover schema and restricted role grants."""
import importlib.util
import re
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[2]
MIGRATIONS={
 '20261029203000_resource_metrics.sql':('table:resource_run_samples','table:resource_run_summaries','table:resource_container_samples','table:resource_container_summaries'),
 '20261029210000_generator_snapshots.sql':('table:generator_snapshots',),
 '20261029220000_generator_documents.sql':('table:generator_documents',),
 '20261030010000_operator_log_records.sql':('table:operator_log_records','table:operator_startup_reports'),
 '20261030020000_resource_metrics_bounds.sql':('constraint:resource_run_samples_finite','constraint:resource_container_samples_finite'),
 '20261030030000_resource_metrics_maintenance.sql':('function:requi_maintain_resource_metrics',),
 '20261030040000_engine_quality_config.sql':('table:engine_quality_config',),
}
ROLES=('requi_metrics_writer','requi_engine_config_read')

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
    if kind=='table':return conn.execute('SELECT to_regclass(%s) IS NOT NULL',('public.'+name,)).fetchone()[0]
    if kind=='constraint':return conn.execute("SELECT EXISTS(SELECT 1 FROM pg_constraint c JOIN pg_namespace n ON n.oid=c.connamespace WHERE n.nspname='public' AND c.conname=%s)",(name,)).fetchone()[0]
    return conn.execute('SELECT to_regprocedure(%s) IS NOT NULL',('public.'+name+'(double precision,double precision)',)).fetchone()[0]

def inventory(conn):
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

def main():
    import psycopg
    s=settings();values=s.load(ROOT)
    if not values.get('DELIVERY_DATABASE_URL'):raise ValueError('DELIVERY_DATABASE_URL missing from protected settings')
    with psycopg.connect(values['DELIVERY_DATABASE_URL'],connect_timeout=10,autocommit=True) as conn:
        conn.execute('SET default_transaction_read_only=on')
        rows=inventory(conn);missing=grants(conn)
    for row in rows:print(row['status'].upper(),row['file'],('REVIEW REQUIRED '+','.join(row['destructive'])) if row['status']!='present' and row['destructive'] else '')
    for role in missing:print('MISSING SET membership:',role)
    if any(r['status']!='present' for r in rows if r['file']!='20261030010000_operator_log_records.sql') or missing:
        print('SCHEMA NOT READY: run python3 scripts/owner/apply-pg-cutover.py . for a dry review. No database changes made.');return 1
    print('SCHEMA READY: cutover catalog objects and app SET memberships present (not a full migration checksum proof).');return 0

if __name__=='__main__':
    try:sys.exit(main())
    except Exception as exc:
        print('SCHEMA CHECK FAILED: '+type(exc).__name__+'; no values printed; no database changes.',file=sys.stderr);sys.exit(1)
