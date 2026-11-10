# 2026-11-10: the watcher read the progress cursor from the database before every drain

Evidence (task 7f3b86686274): one 153 s answer made 28 drains, each starting with store.progress_cursor
(pool check + SAVEPOINT + set_config + SELECT + COMMIT = 5 round trips): progress_cursor 4346 ms and set_config
8649 ms in the trace, about 140 round trips.

Decision (Shy, "Accepted", option B): _watch_engine_advance keeps an in-memory cursor (_ProgressCursor). It reads the
database at start, every 30 s, and after any error; between reads the cursor advances from the pages it ingested.
The final drain after the stop event has no cache and reads the database.

Why this is safe: the cursor is bookkeeping. Ingest deduplicates, so a stale value can only refetch a page; the
engine read and the stop event keep their own checks. Cost: a deleted task is noticed by this loop up to 30 s later.
No authority (stop, generation, bindings, auth liveness, worker jobs, payments) is cached by this change.

Rule: when a read exists only to find where to continue, remember where you stopped.
