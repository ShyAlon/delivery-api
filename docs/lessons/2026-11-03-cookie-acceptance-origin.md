# Authenticated reads are not mutation readiness

Browser handoff moved owner acceptance from a bearer token to HttpOnly session cookies. Six GET readiness checks passed, but the first POST lacked the Origin header required by the unchanged server CSRF guard. The script discarded its HTTP403 evidence and printed only Refused. The repo-owned HTTP client now supplies its verified target origin for state changes. A regression runs the real guard: GET succeeds, cookie POST without Origin refuses, and the same request through the fixed client succeeds. No guard is bypassed or weakened.

Failure reports preserve operation, HTTP status, fixed gate reason and next action. Arbitrary response bodies and driver errors stay private. Missing task_id is not universal proof that creation did not commit: refused creation and an unconfirmed response are separate states. Track whether Start was sent, and never recommend blind retries or pretend a task Debug ZIP exists before a task is known.
