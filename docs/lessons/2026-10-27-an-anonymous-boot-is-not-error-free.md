# 2026-10-27 - An anonymous boot is not error-free: classify, never count

The #272 browser smoke failed the exec preview on a HEALTHY page: it counted
console errors, and an anonymous cloud boot always has some - the tenant
boundary 401s the page's own pre-sign-in probes (telemetry POST, balance
widget, workspace list), Chromium echoes each 401 to the console, and the
page's own clientLog echoes each failed api() call again. Two of those lines
aborted Shy's run at step 9.

Three durable points:

1. "Any console error fails" is only valid when the tested state is the state
   under test. The hermetic boot test runs in LOCAL auth mode, where the same
   probes succeed - the gate was never exercised against real anonymous 401s.
   When a test mocks a seam (here: auth mode), one test must exercise the real
   one: tests/test_preview_browser_smoke.py boots the real page behind the
   real TenantMiddleware (cloud needs Postgres, but a token-less visitor is
   401'd before any verifier or DB call, so wrapping the sqlite app reproduces
   the live boundary exactly).
2. Chromium's "Failed to load resource" console line omits the URL. Never
   classify by console text: capture page.on('response') and classify by
   (method, URL, status); drop the console echo only when a captured response
   accounts for it. The app's own api.error echo carries structured detail
   (m.args[1].json_value()) - classify it by endpoint, and distrust it without.
3. Tolerated does not mean silent: every tolerated probe is named in the
   output, so a change in the page's anonymous probe set is visible (and the
   integration test pins the exact set).

Adjacent defect surfaced (reported, not fixed here): POST /api/client-log is
401 for anonymous visitors in cloud mode, so client errors BEFORE sign-in -
including boot failures and sign-in failures - are silently dropped.
