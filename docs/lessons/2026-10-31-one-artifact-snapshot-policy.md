# One artifact snapshot policy

C05: filesystem and PG document sources used separate declared-set hashes, settled-phase selection and changed/vanished/appeared comparisons. Keep byte acquisition source-specific and hash-verified by its owner. Feed both sources into the same deterministic identity/hash policy for publication-only recovery and settled-work protection. Never import disk bytes into the PG path or use a mutable fallback.

Document read, freshness read, capture and append also share one exact stored-byte hash check. Freshness and tombstone decisions stay with each caller; append still verifies the original bytes before joining new bytes.
