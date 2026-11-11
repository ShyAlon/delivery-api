# A retained rollback container is not a live diagnostic process

The collector listed all preview containers so historical Docker logs would be
kept, then tried Python exports in every one. Docker exec correctly fails for a
stopped container. Its plain-text error was then treated as malformed JSON,
creating three false missing-source warnings for every retained previous run.

Reuse the already-filtered inspect status. Keep Docker logs for all matching
containers; label known stopped/paused containers as skipped and never exec
there. Unknown status still attempts collection and reports actual errors,
rather than hiding a real active-container export failure. Empty answer traces
remain valid before the owner has submitted any answers.
