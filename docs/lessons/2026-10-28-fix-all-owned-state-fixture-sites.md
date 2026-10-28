# Fix all owned State fixture sites, 2026-10-28

The PG workspace State mapping fix did not cover the older cloud and task fixtures.
Search all fixture patch sites and use mapping setitem for store/operator_read_store.
Keep every existing assertion. The old rerun preview test also expects HTTP200,
while the current approved cloud contract safely refuses unsupported rerun409.
Report that separate mismatch;do not make unrelated compatibility work green by
changing the expected status or reopening execution.
