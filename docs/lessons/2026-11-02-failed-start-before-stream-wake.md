# Commit failed Start state before publishing its error event

The failed real Start persisted failed, but the UI remained Working. The old
handler wrote an error event (which wakes readers during its transaction), then
updated phase/task failed in another transaction with no wake. A reader could
see the old task and wait until the60safety. This is not proof that the UI reducer
chose a stale snapshot: that reducer already honors failed control state.

Commit failed state first, off the event loop, then publish the error. A reader
woken by error now always sees failed, even if that particular error row has not
committed yet. Other state-changing notification races are not solved by this
narrow ordering fix and should not be claimed as solved.

The child claimless progress path raises KeyError before publication, unlike the
HTTP404 wrapper. Treat it as absent only when allow_absent is explicit so the
watcher cannot replace the actual Start outcome with a final-drain failure.

New migrated-PG real Start-completion/watcher guard failed first on the escaped
KeyError. It now checks failed state at each wake, preserves the provider error,
and proves failure writes run off the loop. Existing operator-only diagnostics
and customer-minimal evidence assertions remain.
