# 2026-10-30 - PG durability includes process evidence

A PG task/document store does not make a process PG-only while its activity,
stage, engine and diagnostic handlers still write files. Replace each default
writer, its restart reader, and its export together. Keep the previous file
paths until the replacement has merged; retirement is a separate step.

- Use one stable service source across hosts and restarts. The default is
  `delivery-runtime`, intentionally sharing a 32 MiB ring across the service.
  Never derive a source from a PID, container name or random run identity.
- Bound each fixed stream's bytes and rows in the append transaction under a
  source/stream advisory lock. Source-specific custom deployments have their
  own ring; the operator must reuse a stable source instead of creating new
  names on each rollout. There is no automatic deletion of obsolete custom
  sources in this change.
- A restricted process writer cannot read tenant or credit tables. It is not a
  tenant security identity: only the trusted process can choose its source.
  Cross-user reads require the live operator registry `logs.read` capability.
- Missing role/table/source or connection failure is degraded evidence, never
  permission to fall back to a local file. Startup probes the role and tables
  read-only; deployment must grant the app login SET membership in
  `requi_log_writer` separately. No migration or role grant runs at startup.
- Keep pool checkout, lock and statement waits bounded. Never log a database
  logging error back into its own logging path.
- Scrub projection values before serializing JSON. Truncating a JSON envelope
  produces invalid restart evidence. Validate the frame schema and preserve
  integer line numbers. Split a traceback into safe rows before truncation.
- Startup checks use a bounded latest-only PG report per stable source, not a
  growing history or a file. PG server-log operator views and ZIP downloads use
  the registry reader across hosts, not one worker's memory or writer role.
  Downloads omit structured frames and unstructured engine stdout (which the
  previous ZIP also excluded), and cut non-activity messages at 160 chars.
- Customer views retain exact session-owner resolution. Unattributed engine
  lines and continuation rows are not customer evidence.

Changed tests: the startup check inventory increases from 9 to exactly 10, and
its crash matrix includes the new refuse-level process-log check. Existing
file-reference, scrub, tenant, operator revocation and error assertions remain.
New tests cover real restricted-role SQL, append rollback, source isolation,
retention, missing-source non-fallback, recursion, finite DB timestamps, typed
frames, startup restart readback, handler/tee no-file behavior and registry-only
operator view/download. No existing assertion is relaxed. Hosted CI is not
verified by local tests. Live DB writes, resets and deployment are out of scope.

Broader paired cross-host acceptance exposed an event-loop lock inversion:
legacy stdout progress synchronously waited for a task row held by an HTTP
read, while that read needed the event loop to release it. The PG probe showed
an idle document-read transaction blocking task publication/progress, and the
stack showed `ProcessRunner._run -> update_phase` blocking the async loop.
Move stdout ingestion, diagnostics and legacy progress persistence to a worker
thread, await it in order, and retain the same tenant context and assertions.
A deterministic release-from-event-loop test covers the lock boundary. The
initial acceptance timeout and isolated green retry are not discarded.

Explicitly enabling ledger tests found an older descriptor-lifetime fixture
that sent arbitrary `python -c` code through the PG wrapper. The production
wrapper correctly refused it. The fixture now enters the real fixed-module
launch context first, retaining its original job/attempt/PG binding and both
inherited descriptors, then replaces only the child command with the fake
producer. Existing settled-attempt, missing-artifact and no-open-gate assertions
remain unchanged. Separate tests still reject arbitrary wrapper modules.
