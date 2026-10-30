# Failure evidence and cleanup must match the promise

The browser smoke named its errors earlier in output, but an owner tail only
contained the generic ABORTED line. The failure text promised a retained preview,
while the unconditional EXIT trap removed every started container.

Step9 now captures complete browser smoke stdout/stderr and repeats it directly
under the abort line. Only a browser-smoke failure sets the inspection-retention
flag; earlier failures and normal Ctrl-C cleanup remain unchanged. Temporary
settings are still deleted. Retained-container output explicitly warns execution
is still ON against live DB and prints log and stop/remove commands. Browser
smoke never signs in, submits an interview or calls a provider.

No classifier was relaxed. Expected anonymous401s remain only GET /api/tasks and
/api/account/credits. Other401s, every500, assets/CSP/page errors, failed requests,
empty/stuck boot and missing successful anonymous POST /api/client-log fail. The
app emits navigation telemetry on load. An absence can fail quickly even if UI
renders, but the owner's missing named errors are needed to establish the actual
cause. New tables do not excuse500s or widening the anonymous401 allowlist.

Two cases failed first, then real cleanup function + browser classification and
anonymous disposable-PG browser tests passed. No live preview/browser/container,
owner-machine command, provider call, spend, deploy or hosted CI was performed.
This fixes the misleading failure/cleanup behavior, not an unobserved boot cause.
