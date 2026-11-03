# Answer timeout is not a rejected answer

The owner reached a second multi-minute wait after submitting the scripted tutor answer. Delivery synchronously waits for engine submit, then final progress drain. Engine respond publishes an in-memory accepted/running snapshot and synchronously advances the program before its final save/response. The outer client times out after90s while engine HTTP defaults to unlimited. A long response is therefore compatible with next-turn generation; without timings it cannot be attributed specifically to generation versus drain.

Returning accepted from a detached delivery task would not prove durable acceptance. The engine explicitly rolls back memory-only acceptance on some advance failures. A real asynchronous receipt needs a durable engine command/receipt design, not fire-and-forget.

The owner CLI now catches only transport uncertainty on its one answer POST and reconciles with GETs on the same task. It includes the original input ID in the POST. A different nonempty text-input ID confirms advancement; old input, working state or missing ID never does. The original900s answer/next-question deadline includes the submit wait, and no mutation is replayed. Failure retains the task and warns that the client did not Stop server work. Start uncertainty remains separate and is not silently treated as accepted.

New failing-first and hermetic tests prove lost response recovery, old/working/GET-timeout transitions, bounded unresolved outcome, no second POST, missing-input refusal and retained paid-start reference. Delivery names-only timings split submit and final watcher drain for future diagnosis. No live acceptance task was touched, no provider was called, no paid run or production write performed.
