# Preview startup evidence and bounded connection checks

## Owner flow

The existing image rebuild/preview commands stay the entry point. After the
owner confirms the replacement, but before stopping the prior preview, a
subprocess TCP-only check spends at most five seconds including DNS and all
addresses. A failure says the host cannot reach the endpoint and leaves the
prior preview untouched. It does not claim the WiFi, credentials, schema or
Docker network caused the failure. Local socket, multi-host and unsupported
endpoint forms retain their normal container preflight instead of being
rewritten into a guessed TCP destination.

The actual full-image preflight shares one ten-second wall-clock connection
budget across connection attempts. The main-thread startup CLI uses a temporary
POSIX timer, restoring the previous handler/timer afterward. The DSN's host,
TLS and multi-address parameters are not rewritten. Existing connection options
are preserved with bounded startup SELECT/lock waits appended. There is no
application request timeout change. Platforms/threads without this startup
mechanism fail explicitly, not an unbounded fallback.

After the first database prerequisite/connect failure, dependent metrics,
quality, core/feature-table checks are skipped, not retried as independent
failures. The original refusal remains hard and unrelated filesystem, engine
compatibility and configuration checks still run. One first-line root-cause
summary distinguishes timeout, login, missing settings and role/claims failures.
No migration, new schema/role grant or automatic settings change is performed.

## Evidence

- Flushed early entrypoint, environment, per-check, app-import, store and
  lifespan markers name the last stage reached, elapsed time, process and time.
- The image retains bounded private marker and preflight-report files on its
  existing /data volume, not preview's ephemeral /tmp. Startup run IDs prevent a
  prior refusal being used after restart. Delivery inherits the parent's ID.
- On readiness failure, both attended preview paths automatically inspect only
  container state/image and health history, capture the last 80 main log lines,
  and copy the bounded startup evidence. Missing fields/older images are stated.
- The launcher prints sanitized state (status, running, exit, error, OOM,
  timestamps), probe output, last stages/report and probable cause. Running with
  unhealthy probes is not described as a clean exit merely because ExitCode 0.
- Evidence is also retained in private startup-diagnostics/startup-failure.json
  outside the container. No environment/command dump or automatic restart/remove.
  Values are redacted before serialization; known DSNs/passwords/tokens, generic
  credential assignments and ANSI control characters do not become output.

## Proof and limits

Failing-test-first showed three connections after a single timeout; the repair
makes one and records four dependent skips. A real full_entry subprocess with
the installed psycopg driver talks only to a disposable local TCP blackhole:
one connection, exit 2 within 15 seconds, early/per-check logs, root-cause report
and persisted markers/report. This is not a mocked connect-timeout assertion.

Hermetic tests cover timer restoration, no altered TLS/host parameters, private
file permissions, redaction, empty logs with persisted timeout, OOM as a
separate cause, unavailable/older-image evidence, restart freshness, host-probe
wall-clock bound and preservation of an existing preview on host failure.
Local owned-PG/socket and real migrated disposable-PG preflight checks remain.

No owner network, live DB, paid model, deployment or real Docker daemon was used
for this proof. Docker inspect/log/cp behavior is exercised through command
fixtures; the image's /data placement is source-verified, not a new real-image
build claim. Hosted CI is not available; local gate only is the accepted process.

Local validation: 2,457 fast checks passed, one optional skip and five explicit
marker exclusions (run as two independent partitions of the same classifier
list). Wider startup suite: 135 passed, one optional engine skip before the
extra malformed/stale capture case. Built and installed wheel: shared startup
module resolves from the wheel and 17 focused checks pass. Final focused suite
and owned-PG preflight are rerun before commit. No UI changed; boundary checker
reports zero exceptions.
