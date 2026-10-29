# Local shell contracts need their import profile

2026-10-29: held PG-first installs tenant middleware at app import. Local credit
shell/unmounted payment, task rerun-capability and legacy session redirect
contracts reach authentication instead of their intended handler. Fail-first:
3 failed, 19 passed, 1.40s. Do not weaken 404/302/capability assertions.

Move these local HTTP cases into the existing poisoned restricted-PG local
import process. Keep all five old-session page parameters, shell markup/cache
checks, unmounted payment POST/GET 404 checks, no cloud execution, and task
rerun_control assertion. The separate cloud rerun refusal cases are untouched.
Task names are unique for the fixed local test owner; redirect parameter passes
through test-only environment. All 22 cases retained, no assertion/coverage loss,
no production/default-bootstrap or UI design change. This is local contract
proof, not a claim that anonymous cloud requests bypass sign-in.

Purchase UI + tenant rerun + web diagnostics + cloud API, Python 3.11.17:
58 passed current 10.20s, 58 passed held PG-first 10.65s; one platformdirs warning
each. Hosted CI/full suite unrun. No deploy, live DB, provider calls or spend.
Bootstrap restored/unpublished.
