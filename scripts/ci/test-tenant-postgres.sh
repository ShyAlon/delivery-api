#!/usr/bin/env bash
# Hermetic contract run on LOCAL throwaway Postgres. No production DSNs accepted.
set -euo pipefail
cd "$(dirname "$0")/../.."
: "${PYTHON:=python3}"
: "${PGHOST:=127.0.0.1}"
: "${PGPORT:=5432}"
: "${PGUSER:=$(id -un)}"
case "$PGHOST" in 127.0.0.1|localhost|/tmp) ;; *) echo 'Local Postgres only' >&2; exit 1;; esac
unset PGSERVICE PGSERVICEFILE
DB="test_tenant_$(date +%s)_$$"
createdb --encoding=UTF8 --template=template0 "$DB"
trap 'dropdb --if-exists "$DB"' EXIT
psql "$DB" -v ON_ERROR_STOP=1 <<'SQL'
DO $$ BEGIN
 IF NOT EXISTS(SELECT 1 FROM pg_roles WHERE rolname='authenticated') THEN
   CREATE ROLE authenticated NOLOGIN NOSUPERUSER NOBYPASSRLS;
 END IF;
 IF NOT EXISTS(SELECT 1 FROM pg_roles WHERE rolname='delivery_app') THEN
   CREATE ROLE delivery_app LOGIN NOSUPERUSER NOBYPASSRLS;
 END IF;
END $$;
GRANT authenticated TO delivery_app;
-- Local disposable CI credentials, never used against cloud or production.
ALTER ROLE delivery_app PASSWORD 'local-test-only';
CREATE SCHEMA auth;
CREATE TABLE auth.users(id uuid PRIMARY KEY);
CREATE TABLE auth.sessions(id uuid PRIMARY KEY,user_id uuid REFERENCES auth.users(id));
CREATE FUNCTION auth.uid() RETURNS uuid LANGUAGE sql STABLE AS $$
 SELECT nullif(current_setting('request.jwt.claim.sub',true),'')::uuid;
$$;
GRANT USAGE ON SCHEMA auth TO authenticated;
GRANT EXECUTE ON FUNCTION auth.uid() TO authenticated;
INSERT INTO auth.users VALUES ('00000000-0000-0000-0000-000000000001'),('00000000-0000-0000-0000-000000000002');
INSERT INTO auth.sessions SELECT id,id FROM auth.users;
SQL
for migration in supabase/migrations/*.sql; do
 psql "$DB" -v ON_ERROR_STOP=1 -f "$migration"
done
psql "$DB" -v ON_ERROR_STOP=1 -c "INSERT INTO users(user_id,label,created_at,updated_at) SELECT id,'test',0,0 FROM auth.users"
export DELIVERY_TEST_POSTGRES_DSN="host=$PGHOST port=$PGPORT user=$PGUSER dbname=$DB"
PYTHONPATH=src "$PYTHON" -m pytest -q tests/test_tenancy.py tests/test_tenant_rls.py tests/test_supabase_identity.py tests/test_postgres_scope.py tests/test_postgres_store.py tests/test_attempt_ledger.py tests/test_admission_catalog.py tests/test_expense_alarm_policy.py tests/test_operator_expense_snapshot.py tests/test_billing_band.py tests/test_expense_alert.py tests/test_deepseek_usage.py tests/test_cloud_api.py tests/test_tenant_http.py tests/test_supabase_sessions.py tests/test_legacy_ownership.py tests/test_legacy_import.py
