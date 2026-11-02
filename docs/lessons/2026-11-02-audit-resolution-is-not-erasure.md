# Audit view and guarded resolution

Uncertain provider outcomes belong in a private operations audit, with evidence and a warning. They are not a reason to shut down independent admission and are not proof that no bill exists.

The new Audit tab uses a curated event list and allowlisted detail fields, not arbitrary old payloads that may contain prompts. Paging is keyset by audit ID; event filtering is server-side and text filter/sort state explicitly applies to loaded pages. Existing operator logs/session/analytics role boundaries still control joined detail; a hidden joined task is never assumed safe to delete.

Current-owner resolution only. Delete means Stop and hide the related task, retaining accounting, artifacts and audit. Cancel only an undispatched reservation on a non-running job, rechecked under locks. Release only a dispatched attempt with stored exact no-request-byte proof and all existing ledger evidence/rate guards. An uncertain paid call has no eligible Release, and the dialog must explain why. No write-off, refund, invented zero settlement or cross-user mutation.

Show evidence first, then a binary confirmation. Busy confirmation cannot be dismissed; error and success are explicit. A failed/unverified response tells the operator to refresh before trying again. Every successful action writes audit evidence atomically. No live database changes or operator policy grants are performed during development.
