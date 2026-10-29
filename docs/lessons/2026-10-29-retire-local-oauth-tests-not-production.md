# Retire local OAuth tests without changing production (2026-10-29)

Owner explicitly requested removing the11local/legacy auth_quota tests in five
groups. This first theme removes only3local OAuth/session tests:
- test_google_oauth_state_session_and_logout
- test_github_oauth_fetches_verified_email
- test_session_cookie_handle_is_not_stored_verbatim

39test lines deleted;no replacement test/guard,production code unchanged.
Before15passed2.33s;one-off absence check failed while tests present,passes
after deletion. After83passed0skips6.32s across remaining auth_quota,cloud API,
PG bootstrap,Supabase sessions/identity,and retained local session refresh.

Coverage lost:only successful OAuth start/callback roundtrip tests for local
Google/GitHub,only GitHub verified-email fallback assertion,and only direct
local session-row nonplaintext assertion. Production auth.oauth_start/callback
and store OAuth-state/session hashing remain,now lacking those direct checks.
Local browser identity/logout and session-refresh tests still cover local
session use/logout/rotation. Cloud OAuth-refusal,signature/live-session checks,
Supabase cookie/refresh/logout tests retained. No cloud behavior removed.
Hosted CI/full suite unrun. Further production deletion needs owner approval.
