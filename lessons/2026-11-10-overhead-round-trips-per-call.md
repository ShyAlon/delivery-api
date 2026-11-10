# 2026-11-10: three overhead round trips on every store call

**Symptom.** The statement trace of one warm answer showed 74% of round trips were overhead, not work: a pool liveness check on every checkout, a separate SAVEPOINT round trip after BEGIN + identity, and a commit.

**Fix.**
- The pool check is skipped for a connection the delivery path itself returned less than 10 s ago and that has already been through the delivery start. A server-killed connection inside the window fails its first statement and `_begin` drops and replaces it (a reused connection only). Engine borrowers and connections still carrying the new-connection marker keep the full check.
- `SAVEPOINT operation_start` rides the pipelined BEGIN + identity batch (`transaction(savepoint=True)`).

**Caught by the existing suite.** First version skipped the check for any recently returned connection. `test_engine_first_stale_socket_cannot_escape_as_fresh` failed: an engine-used connection still carries the new-connection marker, so `_begin` treats it as fresh and does not retry. The skip now requires the marker to be cleared.

**Rule.** A check that looks redundant may be what a later retry rule depends on. Read who consumes the "reused" signal before removing the check.
