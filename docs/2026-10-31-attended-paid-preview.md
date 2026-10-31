# Attended paid-child preview controls

Code is prepared; paid activation and the first paid run remain separate approvals. Nothing in this change runs a launcher, changes the live database or spends credits.

Before an owner-run preview: review current schema readiness, run paid-child-preflight.py for the exact owner, apply only the separately reviewed policy/profile steps if needed, resolve unresolved accounting, confirm credits and reviewed prices/vendor terms, and obtain explicit paid-run approval including its limits. The launcher does not repair rows or clear caps.

After those steps and approval, the owner can explicitly set PREVIEW_PAID_CHILD_APPROVED=1 when running preview-exec. The existing typed yes is still required. This local control is an operator assertion, not proof or a substitute for approval. Never persist it in shared settings.

The launcher removes inherited child selectors and writes attended-paid plus its separate preview flag only for the managed paid/dual preview. Local-only mode writes local and never constructs a paid runtime. The server constructor requires an explicit preview marker, control-plane-off and an HTTP loopback public origin, then builds the typed runtime from the same reviewed gateway models and parent-only key. The deployed server/default/paid selector stays closed. No key reaches child argv, environment or launch settings.

This prepares installed runtime wiring. It does not establish live vendor availability, Grafana delivery, schema/policy correctness or full browser workflow readiness. The money gateway still validates every real dispatch and can refuse it.
