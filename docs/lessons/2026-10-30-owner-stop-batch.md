# Bulk Stop must preserve per-task proof

A reset dry run found old tasks with durable provider history and pending Stop,
and a failed task whose worker still reported running. The reset must not
rewrite these states to make Delete succeed. Its blockers are safety checks,
not administrative friction.

The owner-run Stop All wrapper first lists only the reviewed blocked population.
A separate typed STOP N TASKS confirmation is required, with a fresh fingerprint
check. It uses the existing cooperative Stop coordinator, not task/job UPDATEs.
The preview's private engine token stays inside its running container. A hashed
DB-target comparison refuses a preview using different settings. No token or DSN
is printed. Missing sessions, absent preview and unverified engine responses
remain blocked. Accepted Stop may persist even when later network work fails;
there is no automatic retry, rollback claim or Delete.

Tests cover failed-task live jobs in dry inventory, typed confirmation, changed
plan refusal, pending/stopped results and per-task error reporting without private
error text. The existing coordinator/child Stop/reset suites also run on owned
disposable PG. No real DB, Docker execution, live Stop, worker termination,
provider call, deployment or spending. Hosted CI and end-to-end preview Stop All
were not run. Startup environment isolation is a separate prerequisite PR.
