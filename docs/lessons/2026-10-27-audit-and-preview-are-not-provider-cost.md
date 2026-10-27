# 2026-10-27: Customer audit is credits, preview is not read-only

Removing provider fields from an API is not enough if the UI renders a static
Interview API cost box with a false No usage records message. Customer audit
summary now renders Credits Used only. Project customer totals and nested audit
money metadata to credits without modifying internal source records. Keep
provider expenses in operator diagnostics, not a customer audit label.

A preview flag does not mean execution is off: preview-exec sets the same flag.
Carry the execution setting into boot config and name it as a setting, not proof
of admission or a running process. Every preview can use real account data;
never imply that delete, stop or operator account changes are read-only.

A missing session in this environment is not proof that the original process
was lost or stopped. Show the last recorded state and direct to the owning
environment, usually the original deployment, to check continuation. Do not
promise another environment is running or that a session can resume.

Regression proof: browser starts with legacy cost fields present and must show
credits only. Endpoint test checks nested USD removal and unchanged stored rows.
Boot tests exercise execution flag on/off. Existing audit filter/sort/scroll and
unavailable-state visibility assertions remain active.
