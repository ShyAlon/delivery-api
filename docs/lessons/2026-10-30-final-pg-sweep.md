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


Validation status before publication: targeted combined gate 183 passed, plus
33 evidence/log/revision checks, and a fresh installed delivery-only wheel
actually booted with disposable PG, health/UI and mutation fencing intact.
A broader first-50-file slice produced 515 passed, 9 skipped and 7 failed.
Four crosshost failures were missing local sibling venv links; all four passed
with the required environment present. Three unchanged file-evidence diagnostics
tests reproduced on origin/main (background task, background thread and terminal
child failure). They expect file reads while the handler writes to PG. They are
reported as pre-existing debt, not counted green. The attempted 115-file batch
was interrupted and is not a complete suite result. Hosted CI and Docker build/run
remain unverified. The full current delivery suite is not claimed green.

Continuation status: do not treat this delivery branch as merge-ready. The three
original diagnostic failures and ten additional diagnostic file fixtures were
ported to source-isolated real PG evidence; 36 checks passed. The first 49 files
(excluding separately expensive crosshost acceptance) now pass 522 checks.
Startup-report fixtures also moved to PG and 15 related checks passed.
Further stopped batches still expose old fixtures in engine_log, Debug task log
collection, item6 interview/full graph, log durability and stage log rotation.
Browser batches were interrupted before completion. File-range logs under /tmp
carry the exact unfinished coverage. Known reds are not waived or disabled.
