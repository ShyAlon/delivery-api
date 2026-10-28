# Session refresh port does not prove renewal (2026-10-28)

A73 uses signed PG for the client single-flight expiry-refresh case. The response
is mocked,so one request across concurrent renewal attempts proves client
coalescing,not real Supabase token renewal. Guard failed first1 in0.68s;final
40passed0skips6.27s with cloud API/bootstrap.

Remaining legacy browser tests contain local-rerun,service-managed settings and
pending diagnostic contracts. Do not change security behavior or drop assertions
to make them look migrated. Production unchanged;hosted CI/full suite unverified.
