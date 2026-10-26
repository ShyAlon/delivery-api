# 2026-10-26 - Count statements, not queries-per-second, on a remote database

Incident: every UI poll of GET /api/tasks/{id} cost 15 database statements;
at ~170ms pooler round trip each, the page felt broken no matter how the pool
was tuned. The fan-out hid in `_task_record`: one innocent-looking helper ran
3 extra SELECTs (phases, gates, artifact count) per task, so `list_tasks`
was 1+3N statements for N tasks.

Lessons:
- The pool cannot fix RTT. Connection reuse amortizes TCP+TLS+auth; each
  statement still pays a full round trip. Statement count per request is the
  latency budget on a remote database.
- A helper that "just reads a bit more" inside a loop is where fan-out lives.
  `_task_record` looked free; it multiplied every task read by 4.
- JSON aggregation (sqlite `json_group_array` / postgres `jsonb_agg` cast to
  text) folds the child rows into the parent statement with one assembly
  point per dialect; the record shape the callers see does not change.
- Pin the budget with a counting test at the store boundary
  (`CountingConnection` / `RequestStats`), or the fan-out grows back one
  innocent SELECT at a time.
