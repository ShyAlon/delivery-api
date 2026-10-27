# Review interface changes against older test doubles

PR #286 made the route list use the gateway's reviewed provider-to-model map
instead of the old singular local/model fields. Real ProductionGateways sets
models for paid-only, local-only and dual-provider configurations. An older
#283 test still mocked only local/model, so it modeled an unreviewed gateway.
The route list correctly failed closed, but the existing positive assertion
failed and blocked unrelated PRs.

Causal check with the unchanged test file and isolated source worktrees:
- Before #286, main d387280: debug-path tests 7 passed.
- #286 merge 702fb1a: 6 passed, the positive default-route case failed.
- Fresh main a30ed0a: same failure before this correction.

Fix the fake, not the money boundary: supply its reviewed models map and keep
every original assertion. Add empty-map and different-reviewed-model negative
cases to prevent a loose compatibility fallback from hiding this mistake.
No production code, auth, RLS or payment behavior changes.

Validation: 129 tests passed across gateway, dual-provider/config, debug-path,
tenant HTTP, cutover, auth/quota, log safety and debug-log copy suites. Tests
use fake provider transport and disposable database fixtures, never live
models or the user's database.
