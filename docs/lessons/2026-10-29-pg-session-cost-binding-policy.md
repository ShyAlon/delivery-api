# PG rejects duplicate bindings before cost selection (2026-10-29)

Owner Q1 approved3linked/unknown/ambiguous PG ports,current full evidence object
assertion,and deletion of3unlinked tests. Additional Agree approves replacing
impossible duplicate-session API409setup with exact PG binding rejection and
original binding/cost intact. Guard+stale exact response red2failed0.94s;direct
port ambiguous setup red1failed3passed1.16s;final cost4passed1.04s,cloud/bootstrap/
retail50passed6.06s,zero skips. Explicit local compatibility mode,not cloud
unbound session exposure. No production change. Deleted exact tests:
- test_unlinked_interview_cost_is_retail_and_downstream_incomplete_is_unavailable
- test_unlinked_empty_usage_and_missing_downstream_usage_are_unavailable
- test_session_cost_rejects_nonexistent_session

Lost direct session-endpoint checks for unlinked archive token pricing,incomplete
spec evidence,empty usage,and nonexistent bundle. Production session_cost fallback
remains,not deleted. Unknown usage404retained;linked2.5credits/$2.5retained with
complete evidence asserted. Impossible duplicate API409branch loses test;stronger
PG store invariant now checked. Hosted CI/full suite unrun;no deploy/model calls.
Rebased onto newly merged436;kept both new fixture guards at conflict. Whole
mixed task/cloud/bootstrap/retail slice161passed0skips22.31s after rebase.
