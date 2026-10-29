# Disposable Postgres must outlive neither normal nor killed test runs

The harness relied on Python atexit cleanup. SIGTERM and SIGKILL skip atexit,
including the process-group kills used for bounded inventory runs. Detached
Postgres processes became PPID 1. Thirty-two idle test servers accumulated and
consumed the browser runner's memory. They were stopped only after checking
exact disposable directory/process identity and zero other client backends.
No data directory was deleted during that recovery.

## Fail-first and fix

A real subprocess starts the harness, publishes its actual postmaster PID, and
is terminated. Before the fix both TERM and KILL left the server alive:
2 failed in 22.56s. The test finally stops only its own disposable server.

A small separate-session guardian now watches EOF on a pipe held only by the
owning test process. EOF works even on SIGKILL. It verifies the expected PID in
postmaster.pid and the command's disposable data directory before asking pg_ctl
to stop it. Abnormal exits preserve data for inspection. Normal atexit closes
and waits for the guardian, then performs the existing delete cleanup.

Four cases exercise TERM/KILL against the parent alone and its whole process
group. No existing assertion is changed or removed. No timeout relaxation,
production code edit, live database operation or default-bootstrap flip.

Harness, abnormal exit, runtime bootstrap and cloud API selection:
46 passed in 8.44s current; 46 passed in 8.65s held PG-first.
Hosted CI and the full suite are unverified. Killing the guardian itself is
outside the promise; normal OS/process-group interruption of the test is covered.

## Browser consequence

Unchanged cumulative eight-gate browser navigation failed in 39.75s at 78 MB
available memory with no swap. After idle orphan cleanup it passed in 10.13s,
with about 1 GB still available at the last gate. No product navigation change
or assertion bypass was needed. Summed Chromium RSS is not unique memory usage
because shared pages are counted more than once.
