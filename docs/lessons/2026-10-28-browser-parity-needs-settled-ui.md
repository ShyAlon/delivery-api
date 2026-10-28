# Browser parity needs settled UI, 2026-10-28

Retain the behavior assertion, but wait for the asynchronous render after reload
before asking whether its heading is visible. A browser test that reads count or
visibility immediately after navigation races the client. Use retrying expectations.

Check production error UX before inventing a contract: duplicate task creation
replaces the form with an actionable error dialog; it does not retain the form.
Verify error/cause/next and the visible next step, preserving the existing contract.
