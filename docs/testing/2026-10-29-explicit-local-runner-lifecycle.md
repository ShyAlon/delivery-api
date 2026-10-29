# Make seven local runner lifecycle receivers explicit

The held PG-first default exposes seven direct ProcessRunner tests that borrowed the global local profile. They run harmless Python subprocesses against scopedPG state,not admitted cloud workers. Explicitly select their original local execution mode in each test. No HTTP middleware or cloud capability checks are removed;no fake session authority is introduced.

Receivers cover shutdown interruption preserving running state,engine-approved phase completion,process release before completion callback,open-gate continuation,next-phase launch failure preserving completed boundary,silent-run stall termination,and heartbeats resetting the stall window. Every original exit/state/phase/event/callback assertion remains unchanged. No production/default-bootstrap change,coverage deletion or assertion weakening. No provider/model calls.

Measured Python3.11.17:
- Future bootstrap baseline7failed2.52s at cloud capability admission.
- Today final7passed2.91s.
- Future final7passed2.68s;restored current conftest afterward.
- Expanded API/tasks/cloud/bootstrap157passed20.71s.
- Full suite/hostedCI unverified. Default flip remains held while other receivers migrate.

Lesson:local lifecycle contracts should name their execution profile rather than silently inheriting import-time config. PG-backed persistence does not turn a local parser/lifecycle test into a cloud worker authority test.
