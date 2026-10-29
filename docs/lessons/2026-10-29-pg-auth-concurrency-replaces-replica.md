# Keep no-lost-auth outcome,delete replica emulation (2026-10-29)

Owner approved signed-PG concurrency test first,then replica-test deletion.
Add test_concurrent_signed_pg_answers_never_lose_valid_session:24POSTs in8workers,
barrier overlap,real ES256/session-registry/RLS requests,fake engine only;24x200
and24durable answer events. Closed runtime first failed1in0.77s;test-only installed
admission then1passed1.19s,three repeats1.19/1.19/1.15s. No production fix implied.
Only then delete test_concurrent_authenticated_posts_never_miss_valid_session.
Old51lines including unused imports removed,new40lines replace user outcome.
Replica fake cursor-overlap/mutex failure mode loses its sole direct regression;
TursoReplicaStore production remains unchanged,other adapter tests remain.
Final113passed0skips8.18s auth/API/cloud/bootstrap/Supabase/refresh/control/retail.
Hosted CI/full suite unrun;no live DB/model call. All11requested tests removed;
new PG outcome retained by explicit approval. Production deletion not included.
