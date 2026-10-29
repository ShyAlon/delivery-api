# ZIP redaction must cover paths too (2026-10-29)

Shy chose task-ID redaction and confirmed throughout the ZIP,including URLs and
log text. A standalone token regex redacted JSON task_id but spared URL path
boundaries,leaving the same ID recoverable. Add task-path and owner-qualified
UUID coverage without erasing short diagnostic paths or standalone session UUIDs.

Exact-ID assertion explicitly changes to [token];new whole-archive absence
assertion catches path leaks. Unit red1 in0.82s,browser red1 in12.21s;final
64passed0skips7.69s including scrubber,actual downloaded ZIP,cloud API/bootstrap.
Downloaded status pixels inspected;ZIP contents verified as text separately.
No deploy;hosted CI/full suite unverified. Fresh environment rebuilt after reset.
