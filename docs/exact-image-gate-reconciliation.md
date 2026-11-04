# Theme 3 local gate reconciliation

WIP, not readiness. CI unavailable, local gate only. Docker build/run, registry
publication, server connection and deployment have not been performed here.

The first broad affected-launcher run is 161 passed / 86 failed. Failures are
not inherited baseline debt: they reference launchers intentionally replaced in
this theme. They are blocking reconciliation work, not accepted exclusions.

## Required migration of assertions

- Native-Mac/server-source build tests -> one local linux/amd64 archive build,
  full source hashes, independent-of-umask readable context, build timestamp,
  no SSH during build, and candidate only accepted after actual local lifecycle.
- Mutable tag reuse -> immutable image-ID inspect plus source/runtime/acceptance
  contract match. No fallback rebuild during deploy, no optional skipped gate.
- Launcher-injected behavior flags/provider picks -> unmodified existing settings
  values, same runtime spec at both targets, no execution-off/attended forks.
  Missing/unavailable route refusal remains a runtime admission responsibility.
- Live schema offer/apply before build -> owned disposable schema for image gate;
  live startup preflight remains read-only. Never apply live schema as part of
  build/preview/deploy. Existing owner migration command stays separate.
- Private Back office shell extraction -> real private listener start/health,
  port collision refusal, positively owned child cleanup, no secret log echo.
- Browser errors/container retention -> actual anonymous browser smoke must still
  be integrated with named diagnostics and retained owned preview on failure.
- Quoted confirmations/color -> Python owner entry typed yes, log-level colors,
  no secret echo, no work before selected explicit stage. No shell prompt hacks.
- Old nine-stage prose/order -> new stage progress, duration/resume identities,
  fail-fast fixed failures and clear next action. Do not replace product checks
  with mere source-string checks.

Each old assertion must be moved to its new owning API/entry or replaced with a
stronger end-to-end contract before its legacy test is removed. No blanket skips
or deletion of these failures. Current new-focused contracts are groundwork;
they do not settle the old product/safety duties listed above.

New coverage locations: test_image_workflow, test_image_receipt,
test_image_runtime_spec, test_private_image_publication, test_local_image_run,
test_image_acceptance, test_image_fixture_services, test_image_local_service_fixture,
test_owner_image_deploy, test_remote_image_apply, test_deploy_image_identity,
test_local_image_preview, test_image_private_backoffice, test_one_image_entrypoints,
test_req_image_entry.

### Build assertion mapping completed

`test_exact_image_build.py` now exercises real temporary git repositories and
archive orchestration with only Docker faked: full SHAs/timestamp, amd64 only,
readable under umask077, untracked secret exclusion, dirty/source drift refusal,
failed build cannot advance evidence, wrong architecture refusal, symlink escape
refusal and silent exit137/OOM diagnostic without secret echo. Mutable final-tag
publication assertions are replaced by private manifest/config receipt tests;
failed deploy cannot rebuild is covered by deploy identity and remote apply
contracts. Legacy build tests may now be migrated to these owning APIs. A real
image build remains unrun here.


## 2026-11-04 12:34 checkpoint

Affected gate: 311 passed, one explicit-engine-source skip across 40 files.
The source-backed entry test was then run and corrected to the actual one-parent
runtime rather than a retired permanent engine daemon: 10 related checks pass.
This correction preserves real subprocess boot, health and anonymous signed-data
refusal. No signed admission is inferred from health.

The shared typed-yes prompt passes a real PTY visibility-before-input test.
A native process now validates the actual disposable PG fixture initialization,
all migrations and zero-cost reviewed pin. It does not validate Docker networking,
mount permissions, unchanged service image startup or the real child lifecycle.

Audit regressions caught and fixed failing-test-first:
- Re-acceptance failure previously left old preview-eligible state. Revoke local
  acceptance and publication/receipt state before build/re-acceptance work.
- Remote settings previously checked only file mode. Validate required values
  with the same owner settings validator before registry access or replacement.
- Public-only fixture context was unreadable under private umask. Normalize its
  public file/directory permissions, with no settings or private key copying.

Broader suite is not complete. Complete 246-case groups 2,4,6,7,8,9,10,11,13,14,15
are green (with their explicit integration skips); group0 has 464 passes;
groups5 and12 complete in four disjoint subgroups each. Group3 excluding 35 long
real graph scenarios has 211 passes. The first twelve graph scenarios have 11
completed passes and one timed-out scenario, not a pass. UI groups still require
complete runs and reconciliation with the four unchanged main failures. An
initial isolated item6 graph check without DELIVERY_TEST_SPEC_SOURCE failed;
rerunning with the explicit source passed. No failure is silently excluded.

Remaining before PR: complete all applicable local tests with a disjoint coverage
ledger, review the exact owner commands, dated lesson, PR body and current-main
base. CI unavailable, local gate only. Actual Docker build/local acceptance is
still Shy's Mac gate. GHCR publication and remote apply have not been executed.
Registry raw-manifest byte handling intentionally fails closed if buildx changes
the bytes; Docker host integration and rollback health must not be claimed from
fake-command tests. No server contact, paid work, live writes, migration or merge.
