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
