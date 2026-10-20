-- Read-only. Shows whether the operator stop audit is switched on. Every row should say "yes".
SELECT 'operator account registered and enabled' AS check,
       CASE WHEN EXISTS (SELECT 1 FROM public.operator_principals p JOIN auth.users u ON u.id = p.subject
                         WHERE p.enabled AND 'logs.read' = ANY(p.capabilities) AND p.registry_version = 'ops-v1') THEN 'yes' ELSE 'NO' END AS result,
       (SELECT u.email FROM public.operator_principals p JOIN auth.users u ON u.id = p.subject WHERE p.enabled LIMIT 1) AS detail
UNION ALL
SELECT 'operator role can read run-stop audit rows',
       CASE WHEN EXISTS (SELECT 1 FROM pg_policies WHERE tablename = 'audit_log' AND policyname = 'operator_stop_audit_read') THEN 'yes' ELSE 'NO' END, NULL
UNION ALL
SELECT 'app login can use the operator role',
       CASE WHEN EXISTS (SELECT 1 FROM pg_auth_members m JOIN pg_roles r ON r.oid = m.roleid JOIN pg_roles g ON g.oid = m.member
                         WHERE r.rolname = 'requi_operator_read' AND g.rolname = 'delivery_app' AND m.set_option) THEN 'yes' ELSE 'NO' END, NULL;
