# Cloud read fakes must model durable input recording

2026-10-29: cloud interview read now calls ensure_waiting_input_event, but its
simple store fake still exposed only add_event. The open-question case failed
with AttributeError on unchanged main. Add the actual method to the fake and
assert the recorded task, question, phase and input ID, rather than making the
production read skip persistence. Original state/live-question assertions stay.

Fail-first: 1 failed, 3 passed (0.60s). After: cloud interview/API/execution slice
44 passed (3.35s current); held PG-first result recorded in PR. Test-only fake
repair and stronger assertions, no coverage loss or production/bootstrap change.
Hosted CI/full suite not run. No deployment, external DB, provider calls or spend.
