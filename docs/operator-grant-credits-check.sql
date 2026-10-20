-- Read-only. Shows whether the operator credit grant page is switched on. Every row should say "yes".
SELECT 'operator may grant credits' AS check,
       CASE WHEN EXISTS (SELECT 1 FROM public.operator_principals p WHERE p.enabled AND 'credits.allocate' = ANY(p.capabilities) AND p.registry_version = 'ops-v1') THEN 'yes' ELSE 'NO' END AS result,
       (SELECT u.email FROM public.operator_principals p JOIN auth.users u ON u.id = p.subject WHERE p.enabled LIMIT 1) AS detail
UNION ALL
SELECT 'grant functions exist',
       CASE WHEN (SELECT count(*) FROM pg_proc WHERE pronamespace = 'public'::regnamespace AND proname IN ('requi_operator_grant_preview', 'requi_operator_grant_credits')) = 2 THEN 'yes' ELSE 'NO' END, NULL
UNION ALL
SELECT 'app login can use the grant role',
       CASE WHEN EXISTS (SELECT 1 FROM pg_auth_members m JOIN pg_roles r ON r.oid = m.roleid JOIN pg_roles g ON g.oid = m.member
                         WHERE r.rolname = 'requi_operator_grant' AND g.rolname = 'delivery_app' AND m.set_option) THEN 'yes' ELSE 'NO' END, NULL;
