# Saving an item trapped the founder in a stale gate

Apply item decisions published new evidence, but the page kept its old review revision and unresolved-item count. The next poll then blocked approval as stale. Reload retained the old revision query. The router compared navigation targets against pathname plus hash, excluding the current query, so Open current gate was incorrectly a no-op.

## Fix

Compare complete resolved URLs for router no-op detection and return the mount promise. After a successful semantic or architecture item save, replace the current history entry with a freshly fetched authoritative gate review. Preserve unrelated unsaved text and valid model/option choices; never transfer old semantic decisions over the new server evidence. Keep external evidence changes fail-closed, preserve unsaved text, and offer a real current-gate link. Ignore late save/poll responses after navigation. Historical interview findings also get a current-review/task link.

## Proof contract

The exact Requirements browser reproduction is red on unchanged main and green after this fix. It checks automatic new revision, restored note, ready checklist, enabled Approve after the next poll, one item-save request and a working historical current link. A second test saves real signed owner-scoped Postgres semantic evidence and reads it back. Query-only navigation/history, phone/desktop views, failed saves, unresolved flags, late responses and architecture remaining blockers are covered. No model invocation or gate approval is needed for proof.

A saved item is not phase approval. Never enable approval merely because the write returned success. Mount current evidence and recompute all blockers, retaining server revision checks. Browser Reload is not a route to current when the URL deliberately names a historical revision.
