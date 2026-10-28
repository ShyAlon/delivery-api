# Native worker SQL must preserve authority

A placeholder port is not permission to change worker recovery. Keep owner RLS, immutable generation/session checks, atomic claim CAS, and explicit live-auth-session reauthorization. Prove one winner under real contention, cross-owner refusal, and rollback after a real native write. A query-family fence must name shared task projections and stop-lifecycle exceptions instead of pretending the entire transitive path is native.
