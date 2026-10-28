# A failed report cache write cannot certify the old cache

Stdout remains the authoritative startup report and a cache write must stay
nonfatal. Return write success and warn once per failed write with category only.
The launcher passes expected startup generation/write status into child environment;
Debug compares cache generation to it and reports stale/unavailable/current, never
claims freshness when launched without the marker. Atomic replace preserves the
old cache for inspection. Failing-first tests cover write failure and stale cache.
The new write-failure fixture correctly produces FileExistsError on mkdir, rather
than NotADirectoryError; no existing assertion was changed.
