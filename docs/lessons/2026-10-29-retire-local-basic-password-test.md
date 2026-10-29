# Remove local password test without deleting production (2026-10-29)

Owner requested removing11local/legacy auth_quota tests in five themes. This
second theme deletes exactly test_basic_signup_signin_wrong_password_and_session.
19test lines removed,no replacement guard;no production changes. Local weak-
password rejection,stored scrypt/nonplaintext check,and wrong-password rejection
lose their only direct assertions found. Production password_problem,
hash_password,verify_password and basic_signin remain. Local signup success,
session/browser/refresh/logout still have other tests;not dead code removal.
Cloud Supabase weak-password/refusal/cookie/refresh/logout tests are unchanged.
No cloud behavior was covered by this explicitly local SQLite test.

Before auth module12passed2.14s;one-off absence check failed before/pass after.
After90passed0skips6.56s:remaining auth,cloud API,PG bootstrap,Supabase sessions/
identity,local refresh,control plane. Hosted CI/full suite unrun. Production
removal needs separate approval. This lesson adds16lines,PR net3lines removed.
