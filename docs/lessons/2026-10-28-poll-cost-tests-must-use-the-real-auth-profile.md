# Poll cost tests must use the real auth profile

A test labeled cloud polling previously switched cloud mode off because its
SQLite fixture lacked verified identity. That misses the path the UI uses.
Sign with the local test key through the real tenant middleware and restricted
PG request database instead. Keep the exact combined-snapshot call count and add
anonymous/foreign-owner checks so a cheap read cannot accidentally bypass auth.
