# One configured operator principal

C01: stop/release/spend/grant routes minted separate ops-v1 principals from an admin actor. The database registry still checked access, but route caches bypassed the explicit startup capability selection.

Use the same lifespan-installed reader as Debug and the other operator routes. Missing settings or a missing configured capability now denies these routes too. Credit grants require explicit credits.allocate; do not silently add it to Debug settings. Live Auth-session and database-registry checks remain at each read/effect. Accepted accounting continuations are unchanged.

Tests now inject the shared startup reader instead of clearing retired route caches. A failing-first regression proves an admin badge cannot construct a fallback principal. No tests or authorization assertions were removed.
