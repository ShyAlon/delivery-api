# A pooler username is not proof of the runtime SQL identity

The dashboard URL began postgres.project-ref while startup reported delivery_app.
The preflight reads current_user on the fresh connection before it sets
requi_backend. No app connection hook switches a fresh connection to delivery_app.
Potential sources include a different higher-priority saved preview.env, DSN
options, or server/pooler defaults. No live check established which one happened.
Do not tell the owner to duplicate credentials or infer a role from a pooler suffix.

The owner migration script now has --identify: read-only settings source paths and
session_user/current_user from the app connection, no admin key requirement, no
saving, no SET ROLE, no grants/migrations. Normal dry inventory prints both app
and admin identities and refuses missing CREATE ROLE/public CREATE capability
before applying any file. The existing admin-role-as-app rejection stays intact.
Identical DSNs satisfy the endpoint comparison but are not automatically reused
or assumed suitable. DDL can still fail for object ownership or managed-service
restrictions; per-file SQLSTATE/primary error and rollback remain authoritative.

Two tests failed before implementation, then identity/capability and the existing
seven-file disposable rollout tests passed. No live DB access, owner-machine
operation, deploy, spend or hosted CI was performed.
