# Persisted diagnostics need matching readers

The PG engine already retained categorization shadow evidence in workflow
documents, but the owner-visible classifications route still read a local
JSONL file. Its tests seeded local files even with a PG store, hiding the gap.

The PG route now reads the exact owned, generation-bound workflow document.
The existing reader checks active auth session, task/session ownership, execution
binding and content hash. Local residue cannot substitute for missing PG data.
No new audience or provider-debug permission is introduced. The provider
execution and final-package cloud fences stay unchanged.

Missing evidence is an empty result with a reason. Unreadable/unavailable
evidence returns safe 503, so the existing frontend availability wrapper cannot
present a failed read as successful empty data. Driver messages/source bytes
are not returned. Size/record and nested shape checks bound malformed evidence. The route also
rejects non-finite JSON, limits join scanning to 64 MiB, and limits projected
answer evidence to 8 MiB. It fails safely rather than silently dropping records.

Two existing classification fixtures now seed PG instead of a local file and
leave conflicting local residue. All original value, joined-answer, no-session,
401 and cross-owner assertions remain. New tests reproduce the missed PG read,
corrupt/nested malformed evidence, failed reads and response/join amplification.
The existing frontend failure test now also covers classifications and opens
the checked details element. Its original assertions remain. A real PG browser test checks
the existing Debug classifications section; desktop/mobile pixels were inspected.
No assertions were weakened, no legacy path removed, no old history imported.
No live DB changes, deploy or spend. Hosted CI remains unverified.
