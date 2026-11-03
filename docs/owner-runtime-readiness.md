# Repairing runtime memberships and owner-test access

Payment-journal reads need the application login to SET requi_operator_payment_read. The migration creates its narrow privileges but did not enroll the app login. The rollout inventory previously checked only four roles. It now names all ten fixed runtime roles, shared with tests. No wildcard grant, human capability, payment activation or paid activation is added.

After merge, the owner runs `req migrate` to review, then `req migrate --apply` only if its named-role plan is approved. Even with zero pending SQL files, the existing APPLY confirmation covers repairing the restricted SET memberships. Do not replay old migrations. Restart preview and recheck.

Marked-test403is separate: the signed-in subject must match an enabled registry row with sessions.operate. Default Back office enrollment intentionally does not grant it. For the separately approved owner test workflow, `req backoffice-access --test-owner` is dry review. `req backoffice-access --test-owner --apply` requires `ENROLL REVIEWED OWNER TEST ACCESS` and verifies the same sole-owner binding before updating that exact registry row and private settings. It adds no cross-owner operation, paid activation or write-off. Restart preview after settings change, sign in and refresh readiness.

The marker403blocks acceptance readiness. Payment-journal is not part of acceptance and does not directly block its dry run. Neither error justifies paid retries, altered money records or broader grants. Real payments remain off until the owner's checklist and cleanup pass are complete.

The corrected SET-only fixture also exposed interview-engine's store/checkpoint paths relying on inherited backend privileges. Upstream interview-engine PR88selects the backend role transaction-locally before tenant claims in one helper. This PR bundles that exact rebuilt wheel for acceptance CI. Both should merge before rebuilding preview from source.
