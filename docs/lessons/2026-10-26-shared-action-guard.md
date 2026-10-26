# 2026-10-26 - One shared in-flight guard for server-call buttons

Incident: the task-creation button did not disable on submit (Enter or click)
and double-submitted. Root causes were structural, not local: `dashboard.js`
`showNew()` had no guard at all, and disabled-state visibility was per-selector
CSS, so most surfaces had no `:disabled` styling even when JS set the flag.

Lessons:
- Log helpers must be log-only. `actionBusy`/`actionIdle` used to toggle
  `disabled` AND log; a settle path re-enabled a button behind the guard's
  back (`keepDisabledOnSuccess` silently broke). DOM effects live exactly
  once, in `runAction`.
- Restore the pre-action state, not "enabled": `runAction` restores the
  button's prior `disabled` value so independently-disabled controls (e.g. an
  unavailable model route) are not force-enabled after an action fails.
- Visibility is infrastructure: one generic `button:disabled` rule; per-page
  `:disabled` styles were how "pressed but looks clickable" shipped.
- Pool budgets are one number across services: POOL_OPEN_MAX dropped 10 -> 6
  so delivery pool + two engine sessions (4 conns each) fit the Supabase
  pooler's 15-client session-mode limit (EMAXCONNSESSION, 2026-10-26).
