# Cleanup is not the original child failure

The phase lifecycle caught a producer exception and called VirtualProcess.kill.
That helper releases a virtual stream with -9. The task runner then called the
failure a killed subprocess even when the actual child had exited 1 and emitted
a validated UserFacingError / ModelStructureError shape envelope.

Keep a typed supervisor exception with its observed child return code and
validated shape-only failure. Finish the virtual stream as failed, rather than
manufacturing SIGKILL during exception cleanup. Real Stop/cancellation still
uses the existing stop path. Feed a fixed, founder-readable classification to
the shared task runner and prefer it to incidental earlier Error lines.

Do not forward raw stderr or arbitrary exception text. Generator contract errors
can embed model output, prompt feedback and private content. The safe structure
classification is not the final rejection reason and must say when that detail
is unavailable. Do not weaken a validator or promise Retry will repair output
without the rejected bytes and the source decision. Retry may spend credits.

Regression tests should begin at the failed producer, and also use a real child
exit through the supervisor, virtual lifecycle, PostgreSQL phase/error state and
owned task HTTP response. Browser-check the shared failure banner. A code-only
assertion that kill returns -9 tests the helper, not its caller's judgment.

This fix does not recover details already stripped from past failures, fix the
rejected ADR, authorize a retry, or change spending, schema or deployment.
