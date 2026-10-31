# Maintenance needs owner-wide job evidence

A transition named one running worker job, but the owner had too many in-progress tasks to find it by opening them one at a time. The existing Back office fetched owner jobs inside its workspace response but did not render them; session detail exposed jobs only after selecting a session. Neither surface included the task title in a nonterminal-job list.

Add the direct owner-scoped, paged query and render named pending/running/paused jobs at the top of Back office. Authenticate the current session and account debug capability on every read. Preserve RLS rather than using a cross-owner operator query for an owner-only need. Do not confuse a stored running state, an old timestamp or a saved Stop request with verified live/stale/quiescent state.

Regression proof: the proxy route returned404 and the store method was missing before the change. Tests exercise the real migrated Postgres query, keyset pagination, title/state projection, terminal exclusion, owner isolation, account capability revocation, active-session HTTP authentication, read-only proxy and browser refresh/filter/pagination/revocation. No existing assertion is weakened. Browser fixtures never contact a provider or a live database.
