"""Supabase default-ACL regression for the session tables (PR A/B schema).

pg_harness models Supabase's default privileges (ALL on new public tables and
EXECUTE on new functions granted to anon, authenticated, service_role). The
session-table chain must still end least-privilege: no PUBLIC/anon/authenticated/
service_role privilege on the nine session tables, and the staged-response guard
function executable by its owner only. Found live 2026-10-24: the guarded apply
aborted on anon's default-granted INSERT on artifact_bytes.
"""
import os
import subprocess

import pytest

DSN = os.getenv('DELIVERY_TEST_POSTGRES_DSN')
pytestmark = pytest.mark.skipif(not DSN, reason='requires isolated migrated Postgres')

TABLES = ['engine_session_journal', 'checkpoint_migrations', 'checkpoints', 'checkpoint_blobs',
          'checkpoint_writes', 'artifact_bytes', 'artifact_manifest_ready', 'producer_results',
          'staged_provider_responses']


def sql(query):
    result = subprocess.run(['psql', DSN, '-v', 'ON_ERROR_STOP=1', '-Atq', '-c', query],
                            text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def test_no_default_granted_role_keeps_table_privileges():
    for table in TABLES:
        leftover = sql(
            "SELECT coalesce(string_agg(a.grantee::regrole::text||':'||a.privilege_type,','),'') "
            "FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace, aclexplode(c.relacl) a "
            f"WHERE n.nspname='public' AND c.relname='{table}' AND (a.grantee=0 "
            "OR a.grantee = ANY(ARRAY['anon','authenticated','service_role']::regrole[]))")
        assert leftover == '', f'{table}: {leftover}'


def test_guard_function_execute_is_owner_only():
    leftover = sql(
        "SELECT coalesce(string_agg(CASE WHEN a.grantee=0 THEN 'PUBLIC' ELSE a.grantee::regrole::text END,','),'') "
        "FROM pg_proc p CROSS JOIN aclexplode(COALESCE(p.proacl, acldefault('f', p.proowner))) a "
        "WHERE p.oid='public.staged_provider_responses_guard()'::regprocedure "
        "AND (a.grantee=0 OR a.grantee<>p.proowner)")
    assert leftover == '', leftover
