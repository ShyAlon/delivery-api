# 2026-11-07 - PR checks on open/reopen/ready only, not every push

`synchronize` re-entered ci-cd.yml on 2026-11-01 (commit b963257) so the
fresh-start acceptance run in #612 would re-run when its PR updated. It also
made every push to every open PR re-run the full four-shard matrix. On
2026-11-02 that produced 59 runs and 1,704 billed Linux minutes in this repo;
the first-run-per-branch share was 703. The owner had already chosen the
opposite rule on 2026-10-10: automatic PR checks fire on opened, reopened and
ready_for_review only.

Restored that rule. A push to an open PR no longer starts CI; the local gate
(scripts/test-local.sh, DELIVERY_TEST_SCOPE=pr) covers pushes until the
planned fast (<=5 minute) PR gate lands, and heavy shards stay available via
workflow_dispatch. If a future acceptance flow needs a run on updated code,
trigger it explicitly instead of re-adding synchronize for every PR.
