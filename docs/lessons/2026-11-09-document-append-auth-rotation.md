# 2026-11-09 Rotate document ownership before the guarded insert, not after

## What happened
A founder resumed an interview the morning after starting it, under a fresh
sign-in. Every answer turned 8 was rejected: the engine's very first
PG-document append (the llm-calls.jsonl audit line, written before any model
call) died with InsufficientPrivilege, the child exited with error_class
InsufficientPrivilege, and the supervisor respawned it five times before the
delivery API returned 409. The run looked like an engine crash; it was a row
level security policy doing exactly what it was written to do, in the wrong
order.

The chunk insert policy (tenant_chunks WITH CHECK) demands that the parent
document's auth_session_id equals the caller's current auth session claim.
The document row was created under yesterday's auth session. The append
function rotated auth_session_id to the caller only AFTER the chunk insert,
so the guarded insert always saw the stale owner and every append under a new
sign-in failed. The rotation UPDATE existed; it was just sequenced behind the
one statement that needed it.

## Lessons
1. When a guarded statement and its guard-relaxing update live in the same
   function, order is the bug surface. Relax first, then act - inside one
   transaction with the row FOR UPDATE, so a later failure rolls the
   relaxation back with it.
2. "Same founder, new auth session" is the normal case across any overnight
   resume, not an edge case. Every tenant-scoped write path needs a test that
   writes under one session and appends under the next sign-in of the same
   owner; pg_harness seeds auth.sessions rows, so seed the second session in
   the test (requi_session_active gates all requi_backend RLS on it).
3. The first engine write is the audit line, not the model call. A permission
   failure there produces a child exit with zero LLM activity - instant
   failure, no tokens, no compute evidence. That signature means "check RLS
   before checking the provider."
4. A schema-readiness probe that only checks a function's signature cannot
   tell the fixed body from the unfixed one. The function-body probe kind
   (pg_get_functiondef marker) proves the rotated ordering is actually live;
   use the full typed signature - a bare to_regprocedure name does not resolve
   here.
5. to_regprocedure('public.f') with a bare name returned NULL in the harness
   even with one overload; always probe with the complete argument list.
