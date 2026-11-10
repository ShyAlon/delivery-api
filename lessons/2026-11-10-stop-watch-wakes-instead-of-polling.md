# 2026-11-10: the stop watcher woke every 5 s to ask a question nobody had answered

**Symptom.** The statement trace showed the stop/revocation watcher at 15 round trips per 5 s cycle, about 21 cycles per answer, to learn that nothing changed.

**Fix.** `stop_watch.watch_for_stop`: the Stop request commits through this process and notifies `task-stop:<task>` on the event bus, so a Stop click interrupts at once. Revocation, a generation change and a Stop from another process are read on a 30 s safety read. Same three checks, new cadence. A cancelled watcher releases its waiting thread.

**Single-process assumption.** The wake is in-process, like event_bus. A second server would not wake this watcher; the 30 s read bounds that.

**Rule.** Replace a poll with a wake plus a bounded safety read, and name what the safety read still covers.
