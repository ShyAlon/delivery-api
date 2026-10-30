# A runtime cutover needs build and operational cleanup too

A source grep after the PG replacement merges found dead transcript/retry file
fallbacks, a libSQL extra, build/Docker driver installs, default SQLite DSNs,
and two operational helpers targeting only the retired store. Remove those
only after their PG capability is merged. Keep catalog content, history,
explicit unsupported-backend refusals and negative security guards.

ops-backup.py and its six old-store tests were retired. Its PG replacement and
three tests remain. ops/latency_compare.py was deleted, not ported: it compared
only the removed store and old replica. measure-pg-slice now names current PG
coverage without referencing retired tests. The old libSQL native-build test
was replaced with a required-PG dependency/error-reporting test.

The installed-wheel smoke must actually boot, not merely check for a missing
DSN. Its supervisor rejects inherited DB configuration, starts disposable PG,
applies this checkout's migrations and supplies the restricted app login. The
network-none Docker gate uses a test-only derivative with the same setup. The
real runtime image gains no fake auth or test DB bootstrap. Full-image smoke
also requires the explicit supervisor-owned DSN and keeps its health, token,
static-asset, healthcheck-command and engine-death assertions.

The spec workflow read API now takes a directory, not a dummy database filename;
merge the companion spec cleanup first. PG checkpoint tests keep exact source,
namespace, Stop-race, stale checkpoint and revoked-job assertions.

No live database action, reset, deployment or spend. Docker build/run and hosted
CI remain unverified; local installed-wheel boot is a separate concrete check.

Broader enabled same-host checks exposed three pre-existing reds, reproduced
against merged spec main: two rework fixtures still constructed real SQLite
state with no hosted runtime, and one virtual-runner fixture used an unrelated
artifact root. The rework fixture now seeds real PG checkpoints through a
claimed job and supplies the normal host runtime. Source immutability is an
exact checkpoint tuple assertion; stale-gate and owner checks are unchanged.
The runner fixture now supplies the exact authorized root instead of bypassing
its path checks. No production guard was relaxed.


The expanded suite exposed additional file-evidence fixtures. They now read the
same PG log/report/registry surfaces as production, preserving source, task,
retention, redaction and degradation assertions. The volume test feeds the real
bounded buffer handler without generating thousands of unrelated PG diagnostic
rows. Shutdown uses real PG, signed synthetic auth, SSE and a registered child.
The full item6 graph seeds a claimed real checkpoint/runtime, not a file store.
Pool unit tests isolate their registry rather than closing another fixture's
borrowed pool. The private pipeline no longer closes a pool it does not own.

Two integration regressions needed production fixes: delivery still passed the
removed file-session switch to the engine router, and zero-model final exports
called the now-retired standalone CLI. Exports now invoke the same pure handoff
builder in the isolated subprocess. Published journal evidence also exposed a
Delete FK failure; tasks with that evidence are tombstoned, preserving the
journal instead of deleting its session. A regression test verifies retention.

## Validation

All 345 top-level test files were exercised in disjoint bounded batches against
owned disposable PostgreSQL, with explicit sibling source paths. The large UI
file completed all 159 cases in six disjoint batches. The PG store file completed
all 84 cases in seven disjoint batches. The nine cross-host cases, including the
full alternating-host session through validation, passed across bounded runs.
The decision-load tracing case passed with its full real-browser interview.

The three connected item6 cases and 29 private payment cases were explicitly
enabled on owned disposable databases, not left as environment skips. Both real
zero-model package integration cases passed with the sibling export environment.
Full-smoke now constructs an actual no-engine interpreter and owns its PG
supervisor, eliminating the old environment-dependent skip while retaining the
stage/reason/entry-report assertions. Aggregate disjoint coverage: 3,481 passed,
no skipped cases left unexercised. This is batched coverage, not a single-process
full-suite run; interrupted attempts and overlapping focused gates are excluded.

A clean installed delivery-only wheel previously booted through the disposable
PG supervisor, with health/UI and mutation fences verified and no engine/provider
packages present. Docker build/run and hosted CI remain unverified. No live DB
mutation, reset, deployment, provider call or spend. Merge the spec cleanup
companion before delivery, whose integration checks use its directory read API.
