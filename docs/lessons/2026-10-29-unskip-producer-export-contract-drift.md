# Run producer integration before claiming fixture readiness (2026-10-29)

Missing producer env hid2real red package cases. Restored producer main
1b187471fabc6a59a3c530443f79ab5b1340d4f4;uv sync;both cases fail2in3.42s because
fixture0.27.0 is rejected by current0.28.x contract gate,before intended ledger
validation. Supported migrate validates intent/conops/baseline/architecture then
re-stamps;remaining needs/SRS/item/test/validation models validated before and
after recursive version-only restamp. No behavioral fields changed. Nine JSON
files now0.28.0. Original200/409/findings-ledger/ZIP/immutable assertions unchanged.
Actual producer CLI zero-model export2passed0skips3.02s after fix. PG port remains
separate;this fixes pre-existing hidden red first. No production/deploy/model
calls. Hosted CI/full suite unrun;producer not a deployed topology claim.
Final package integration/final-package/cloud API/bootstrap46passed0skips6.96s.
Compared JSON recursively excluding contract_version:all remaining values equal.
