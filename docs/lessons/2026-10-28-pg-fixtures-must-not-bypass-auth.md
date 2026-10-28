# PG fixtures must not bypass auth

A PG store paired with a one-letter identity verifier still misses production JWT and session-cache defects. Use a local signing key with the real verifier and live restricted-role sessions. For app bootstrap, convert harness conninfo to an actual postgres URI before factory selection; poison SQLite/Turso construction to prove no fallback. Keep leftover fixture inventory visible instead of declaring an entire phase done from one browser migration.
