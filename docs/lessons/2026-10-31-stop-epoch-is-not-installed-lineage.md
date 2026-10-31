# A Stop epoch is not an installed execution

The owner stopped task c8ce3192108b on build 9cd2cbd8. The request returned 200 at 04:43:17 UTC. The next task GET, reference 998dd907, returned 404. The exported DB projection retained stop_requested, generation 2, and four done jobs.

Cause: normal interview Stop increments engine_sessions.generation. The installed-source router treated every generation above one as requiring a revision-installation anchor. The next snapshot therefore raised TenantAccessDenied even though the task existed and Stop had committed.

The router now returns to normal interview control only for the exact first execution's durable stopped successor: lease generation 2, stopped=true in retained control, task generation 2 and a durable task Stop timestamp. Other missing installation bindings still refuse. No worker or provider state is rewritten.

Fail-first disposable-PG test used the actual engine stop store, then reproduced the missing-binding exception. Added a negative uninstalled-execution test. Existing stop, transition and batch tests remain unchanged except stronger transition-message assertions.

Operator transition refusals now name task/job/state and, where relevant, attempt or compute segment. They do not tell operators to delete rows or invent settlement. One blocking row is shown at a time; rerunning revalidates all conditions under the owner lock.

A Stop HTTP success and the refresh after it are different outcomes. Diagnostics and dialogs must not describe a failed status read as a failed Stop or encourage restarting work.
