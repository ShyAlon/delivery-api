# Epoch invalidation does not need an eager cache sweep

Every cache read already compares its store epoch before returning a deep copy.
Invalidation only needs to increment that epoch. Walking the bounded entry map
on every SQL write redundantly deleted entries which could never pass the next
version check. Retain epoch checks, pre/post-transaction invalidation, event-bus
versions, tenant keys and TTL. Stale entries stay bounded by MAX_ENTRIES and are
evicted by normal puts. Do not weaken commit-race fencing or cross-process TTL.
