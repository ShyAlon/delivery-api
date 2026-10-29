# Repair observability test doubles for current native interfaces

Two baseline observability tests failed before reaching their intended marker assertions. Reproduced after merging the retirement PR, on main55f9e7b:2failed in0.58s. The worker capability lacked session_id, required by diagnostic binding. The prepaid fake exposed obsolete _db.execute rather than _execute_native, required for the owner advisory lock and locked charge/account reads.

Only test doubles change. The worker test now supplies the session identifier and requires the original handler exception text, ensuring a failed failure-record write does not replace it. The prepaid fake now explicitly checks native SQL ordering and bound parameters: owner advisory lock, charge FOR UPDATE, then missing account FOR UPDATE. It rejects writes and extra statements, requires the specific held-settlement exception, and confirms pending/no debit state. Existing marker assertions are unchanged. These strengthen assertions, not weaken them. No production files changed.

Measured validation:
- Red:two selected tests failed in0.58s.
- Green:all16observability tests passed in0.67s.
- Expanded task/cloud/bootstrap/tenant availability/PG phase gate/question-quality API/observability slice:173passed in20.81s.
- Worker/dispatch/control-plane/run-slots:21passed in1.23s.
- Full suite and hosted CI unverified. Hermetic fakes prove marker paths and interface expectations, not real database transaction or RLS behavior.

Lesson: diagnostic and PG interface changes need their hermetic receivers updated at the same time. Missing fake attributes are real red tests, not a reason to relax the exception or skip the test. Model the actual seam and preserve the business assertion.
