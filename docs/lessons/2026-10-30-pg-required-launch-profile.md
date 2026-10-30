# A default flip includes the launchers and their children

The local/source and packaged launchers defaulted to a host-local database while
server execution already used PG. Configuration now requires an explicit PG DSN
and the existing Supabase Auth settings. No missing or non-PG configuration can
silently select the former storage tier.

Launch validation is read-only and does not connect. The launcher no longer
prints credential-bearing DSNs or forces local basic authentication. Engine
children receive the same resolved database profile, tenant mode, and a required
private service token. A wrapper propagates dotenv values to the child without
shell evaluation, forces file persistence off, and does not print the values.
Engine validation happens before replacing running processes.

The default test import now selects its own disposable PG server and restricted
app role, ignoring inherited DSNs. It keeps real signed claims/session checks
and substitutes only the JWKS transport. The isolation subprocess still proves
the exact owned database/user/host and no caller database use.

Coverage changes are explicit. The config read-only cases retain every no-home
write assertion, but now expect rejection for missing/non-PG profiles. The old
packaged local-file persistence assertion is replaced by an external PG profile
contract: local database-file creation is intentionally no longer a capability.
The stage-file log test now explicitly selects its reference profile and keeps
all its file, message and phase assertions; PG no-file logging remains covered
by the existing PG operator-log tests. No tests deleted or skipped. No behavioral/security assertion was relaxed.
PG driver/pool are now required dependencies and included in the packaged
runtime dependency set. Lock metadata adds those already-pinned packages without
unrelated version/marker churn.

Fail-first launcher/profile and engine-environment tests preceded implementation.
No auth/OAuth/session/email-availability implementation is removed in this hop.
The legacy adapters, checkpoint fallbacks and dependencies retire separately
after their merged PG replacements, with their own coverage accounting.
No live DB changes, task resets, deploy or spend. Flake tracing is retained.
Hosted CI remains unverified.

Enabling the engine-source-gated full-image smoke exposed its old file profile.
It now uses the owned disposable PG fixture and tenant engine mode, keeping its
health, token-challenge and shutdown assertions. That real boot found the metrics
table access checker still expected the backend role despite the restricted
metrics-writer grants. A fail-first policy test and checker correction fix the
startup blocker without any new database privileges. Four report-file cases
explicitly retain the legacy reference profile and all serialized/error checks.

Two local provenance cases now explicitly provide a reference store (without a
PG marker), preserving their original absent-vs-unavailable and no-invented-turn
assertions. Their former ambient default store became PG in this hop. All 50
additional auth/config/local profile cases pass. The complete suite collects
3489 cases; the full suite is not claimed run in this hop.
