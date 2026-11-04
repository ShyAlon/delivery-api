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

## 2026-11-04 12:55 checkpoint

- Affected gate with explicit sibling sources: 312 passed, no skips before the
  final remote-host architecture refusal regression. That regression and its
  related deployment group pass 17 checks; final combined rerun follows.
- All 35 long retained graph scenarios now complete and pass individually.
- All 160 UI cases complete: four unchanged main failures, 156 passes after
  repeating block1 (40/40). Its first run additionally failed the route-switch
  Escape dialog; isolated rerun and full-block rerun pass. Keep this transient
  failure visible, not a fifth accepted exclusion.
- Broad group13 with explicit source now passes all246 instead of38 source skips.
- Enabling additional fixture inputs exposes two unchanged integration failures:
  item6 connected graph lacks an operator expense policy; task-review lifecycle
  uses the retired execution path and Start returns503. Both reproduce against
  detached main a9e5851 with main imported explicitly. Parent scope decision:
  separate inherited execution-fixture debt, named in PR, no skips/assertion edits.
- No single full-suite pass is claimed. Some original skips remain dedicated
  isolated payment/item6 requirements or unavailable producer export environment.
  The source-backed item6 runner itself produces2 pass/1 inherited failure.
- Mandatory Mac unchanged-image gate remains unrun. PR must be draft/not ready
  to merge until that gate and owner review. See exact-image-owner-workflow.md.

## Mac startup failure, 2026-11-04 16:13-16:21

Owner review passed and the actual amd64 candidate built (94s). Local acceptance
failed at startup: health did not pass. This is a failed gate, not accepted image.
The old implementation waited to timeout even when a container exited, then
removed containers/scratch without saving the reason. The old run cannot provide
container logs after successful cleanup. Do not claim a startup root cause from
this one-line failure.

Fix: inspect owned fixture/candidate state each poll, fail immediately on exit or
Docker unhealthy status, and include the last health exception for timeouts.
Before cleanup, retain a private local-failure.log with exception class/reason,
container state (exit/OOM/health/error), bounded stdout+stderr log tails, and an
in-container healthcheck on a still-running candidate to distinguish internal
startup from published-port reachability. Print the same redacted evidence in
the terminal. Read only invocation-owned labels and state, never Config.Env;
redact handoff token/DSN, URL credentials, key fields and JWTs. Resources still
clean up after evidence capture. Signed HTTP failure output is retained too.

The real native fixture test now runs the actual full-entry preflight and starts
its delivery child to HTTP health with the fixture DSN and public CA. It passes;
this narrows but does not establish Docker-only networking/mount/dependency
behavior. Combined affected gate before final HTTP-tail addition319 pass.
Mac rerun still mandatory. New source/acceptance contract intentionally uses a
new run key: no carrying old acceptance across a changed diagnostic harness.

## Mac internal-only publication failure, 2026-11-04 19:12

The second Mac run built successfully but host health refused while container
health and startup were healthy. Cause class: internal-only bridge publication.
Docker accepts the requested binding but has no default-gateway network on which
to publish it. The source discussion explicitly reproduces this on macOS:
https://github.com/moby/moby/discussions/53256
Official internal-network semantics:
https://docs.docker.com/reference/cli/docker/network/create/

Fix: service stays on its owned internal-only network, without a false publish
claim. An owned, bounded fixed-destination TCP relay alone connects to both the
internal and a new normal ingress bridge; it publishes127.0.0.1:port:8000. It
forwards bytes only to the named owned service8000, never interprets HTTP Host or
CONNECT as a destination. No app image overlay, behavior flag, added egress route
or Internet-enabled fixture. The same Target owns origin and publish binding.
Read actual docker port mapping and print probe URL plus actual bindings on
failure. Probe HTTP first; once container is healthy, a refused host probe or
missing mapping fails immediately. Total wait uses a90-second monotonic deadline
rather than240 iterations of potentially60-second Docker reads. Per-poll Docker
commands are bounded to3 seconds. Actual Mac networking remains to be checked.

Build slowdown: changed committed diagnostic files invalidated the whole-source
COPY layer before wheel building; product wheels then invalidated third-party
pip installation, and --no-cache-dir forced downloads again. This is a proven
cache-invalidation design flaw, not proof which step consumed the reported1555s.
Separate requirements-only COPY/install from product wheel COPY/install and use
BuildKit pip cache mounts. Record plain BuildKit output to private build.log and
fixture-build.log, and print committed context file/byte counts. No claim that
large untracked data entered the context: git archive excludes it. Exact1555s
attribution needs the retained plain build log from the next run.

### Mac child EOF diagnostic follow-up (November 4, 20:59)

The e88c728 Mac gate crossed ingress and signed HTTP, then the real Start child
closed its descriptor without a terminal event. Container health and execution
readiness were not proof of this child operation. The cause is not yet known.

Supervisor now waits for bounded reap/stderr drain and adds observed exit code,
signal, state, sequence, and validated stderr exception class/file/function/line
to the raised failure and container stderr. Negative exit codes may reflect
supervisor cleanup, not the initial cause. Arbitrary stderr remains withheld:
redacting known secrets cannot guarantee removal of project/prompt text. The
existing shape-only stderr envelope supplies bounded source-free evidence.
Owned local-failure.log also captures the bounded private diagnostic file.
HTTP failures name method/path/status and bounded redacted response text;
transport failures are distinct, and the acceptance entry preserves Refused's
reason rather than only its class. No state-changing operation is retried.

Affected local gate: 355 passed (including real exit-23 envelope propagation,
existing stderr flood/privacy checks, HTTP-500 response redaction and transport
refusal checks). This is not unchanged-image acceptance or a Mac pass. The next
Mac log line needed is `child exit=... error_class=... stderr_frames=...`.
If no envelope exists it explicitly reports `stderr_shape=unavailable`; a
negative code alone does not establish an OOM or original cause.

### BrokerUnavailable cause reproduced and fixed (November 4, 21:10)

A new real-child integration uses the image's actual zero-cost fixture HTTP,
production admission catalog, zero account balance and reviewed `phi4` pin.
With a route-only Start (the HTTP acceptance request shape), it failed before
transport with the same BrokerUnavailable, exit 1, running/sequence 2 and SDK
frames as the Mac log. Parent evidence identifies AdmissionDenied: the child
requested unreviewed `ollama/qwen2.5-coder:14b`. Preview had correctly resolved
`phi4`, but ChildInterviewClient._execute used the old hard-coded default when
no paid runtime existed. This is not a child TLS/DNS/environment problem.

Dispatch now obtains the reviewed immutable task factory model, rejects an
explicit conflicting model, and never substitutes the legacy default on this
production path. The same test now completes actual Start and answer in fresh
children through parent HTTP, zero-cost reservation/settlement and retained PG.
Existing fake-constructor seams retain their explicit test behavior.

Child stderr envelopes now include up to six cause class names without values.
Parent broker failures also print a bounded shape-only class/frame/cause line,
since a child's channel EOF cannot reveal a parent's transport/admission cause.
A regression proves ConnectError plus SSLCertVerificationError appear without
private host/token messages and without a second provider request.

Affected gate: 367 passed. Additional safe-envelope, operator diagnostic and
user-message targeted checks passed in a separate 58-test run. No Mac/Docker
pass claimed. Rerun with a fresh mac-modelpin run directory before accepting.
