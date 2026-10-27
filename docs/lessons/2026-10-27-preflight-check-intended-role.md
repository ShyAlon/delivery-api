# A private table is not broken because the backend cannot SELECT it

The startup preflight correctly moved existence checks from information_schema
to pg_catalog, but still asked whether requi_backend could SELECT every table.
That contradicted ten table access contracts in the checked-in migrations:

- payment_purchases, payment_credits, payment_events: requi_payment reads.
- operator_state_events: requi_operator_read reads.
- credit_adjustments, expense_guard_policy, expense_guard_episodes,
  operator_principals, retail_scale_migrations, user_task_policy_changes:
  owner/function-only; direct backend SELECT is intentionally denied.

The preflight must test intended role access, not recommend a blanket grant.
A missing intended role or missing required SELECT remains a warning for an
optional table. Direct backend SELECT on an owner/function-only table is also
an access mismatch, not a reason to grant more. Core failures still refuse.
operator_failure_diagnostics expects backend SELECT and its missing migration
remains a named warning; it is not one of the intentional denials.

The catalog probe resolves roles before checking privileges, so an absent
optional role does not abort the whole check. No SET ROLE to an optional role,
DDL, role memberships, policy changes or broad grants are added.

The real migrated-Postgres regression failed first on main, reporting ten
intentional denials as broken. Tests cover migrated healthy ACLs, missing role,
revoked intended-role SELECT, accidental private-table backend exposure and
missing diagnostics migration. All mutation fixtures are isolated test DB
transactions and roll back. 40 startup/full-entry/preflight tests pass, no skips.
Hosted CI, Docker runtime and full-repository suites unverified. No live DB
access, deploy or restart performed. A table SELECT probe is an ACL contract
check, not proof that every function/RLS/column/write path is healthy.
