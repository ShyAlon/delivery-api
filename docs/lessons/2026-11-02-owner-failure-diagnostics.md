# Boolean-only must not mean reason-free

The access enrollment caught every exception and emitted only completion=false.
The owner could not tell a settings failure from a registry mismatch or connection
failure. A safe output restriction is not a reason to discard all diagnostics.

Use the shared owner_diagnostics classifier in enrollment, configuration checks
and configuration setup. Emit fixed stage, exception class, bounded SQLSTATE and
known reason names as booleans. Do not print raw exception text, SQL, DSNs, paths,
identities or setting values. Unknown failures still name their exact stage and
exception class; they do not guess a missing row or turn an unverified check into
absence. Include intermediate settings/target/registry match evidence.

The existing config-check command is read-only from connection startup and emits
only names/booleans. Enrollment stays dry unless --apply and the unchanged exact
confirmation succeed. Preserve separate DB-updated and settings-saved results.

A later plain read-only connection succeeded in under ten seconds. Both earlier
startup and transaction-only modes had timed out at ten seconds, so startup
parameter rejection was ruled out. Do not turn a transient timeout into a
configuration or role repair. Owner config/access connections now share a bounded
30-second connect window, with no automatic retry and unchanged read-only guards.
The migration tool remains unchanged; this is not evidence that timeout caused
the incident or that the service was paused.
