# 2026-10-27 - Browser smoke: safe shape next to a live database

After the identity.js boot break, Shy asked for a smoke that loads the real UI
entry and fails on any console error or a boot stuck on "Loading workspace…",
"if it can run a fake login even better." The split that keeps the live
database safe:

- preview-exec step 9 (runs against the LIVE database, real sign-in):
  anonymous and read-only BY CONSTRUCTION - load / like a visitor, assert the
  loading marker clears, #app renders, and zero console/page errors. No forms,
  no sign-in, nothing that can write an account, row, or ledger entry.
- The fake login lives only in the hermetic pytest
  (test_boot_smoke_zero_console_errors_anonymous_and_after_fake_login), which
  signs up through the real UI against a throwaway sqlite file.

Check design: two signals, not one - "loading marker gone" (a syntax-dead SPA
never clears it) AND "zero console errors" (a boot can clear the marker and
still be broken). On a stuck boot, print the collected console errors before
exiting: the error line IS the diagnosis. Exit 2 means "chromium not
installed" so the step can print the exact install line instead of failing
the preview for a missing tool.
