# Scheduled renewal failure must update visible sign-in state

A scheduled renewal's rejection was ignored. On the existing expired-session
response, clear the expiry and notify identity once. Show a nonmodal sign-in
notice without rerendering the task or touching draft values. Open sign-in in a
new tab so the unsaved page stays intact. No mutation retries, session lifetime
changes or recurring toasts. Network transport failures retain existing behavior;
only the existing rejected renewal marks expiry. Failing-first event regression
and browser draft-preservation checks cover it.
