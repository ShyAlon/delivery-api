# Make server work visible without claiming an outcome too early

## Problem
The private Back office could leave an operator waiting on an unchanged page while reads, model reviews or maintenance requests were in flight. Button disabling alone did not explain what the server was doing. A spinner without text would have the same problem.

## Change
Both applications now use one operation-status module. It owns lifecycle state, action/request labels, pending request counts, accessible live text, the animated busy mark and the success/error presentation. The private Page footer stays in the viewport; Page owns its positioning and reserved document space. Shared Dialog also mounts the same status view because native modal backdrops obscure the page footer. Closing a submitted dialog closes only the view, not the request.

Labels are UI-owned and do not print request bodies, full URLs, credentials or server error prose. Confirmed task actions identify the task. HTTP and transport failures cannot leave a nested action green just because its page handler catches the error. Requests bind to the latest active action when they start; an older background read failing later cannot mark a newer action failed. Unrelated successful reads cannot erase an action outcome. This is UI request progress, not provider/worker execution telemetry or a guarantee of worker exit.

## Evidence
Deterministic browser promises cover overlapping reads/actions, retained action outcomes, transport/HTTP failures, a read failing after a newer action starts, and redacted labels. Real Back office UI tests delay the stop/delete service request, close the modal while pending, and verify busy/success/error text on the fixed bottom bar. Desktop and 390px phone viewport screenshots were inspected, including the native modal's own status. The initial screenshot check caught the footer blurred behind a native modal despite passing DOM tests; adding the shared view inside the modal fixed that gap. Main app signed-in shell and sign-in reload tests exercise the same module.

No live task, database, provider or payment action was used. Existing auth, confirmations, accounting retention, request timeout and cancellation behavior remain unchanged.
