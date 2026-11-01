# Startup must enforce projection privacy, not require broad grants

The fully migrated isolated image smoke refused because the table-access map defaulted new core projection tables to requi_backend SELECT. Admission entries and all three expense projection tables are deliberately private behind SECURITY DEFINER functions. Their migrations revoke backend direct access. Keep their presence required, but require no direct SELECT for these four entries. Do not change migrations, broaden ACLs, skip the gate or patch the live instance.

A migrated-PG regression now runs the core check as the restricted application role, passes with actual private ACLs and refuses an accidental backend SELECT grant. The original feature-only real-PG check did not cover core projection tables. Verify the complete access map, not only optional features.
