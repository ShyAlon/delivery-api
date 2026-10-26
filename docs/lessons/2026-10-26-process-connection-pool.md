# 2026-10-26 - One process connection pool (delivery-api owns, engine rides)

## Problem
The Supabase pooler (session mode, 15-client budget) was exhausted
(EMAXCONNSESSION): delivery-api ran its own hand-rolled pool while each
in-process engine session opened its own connections on top.

## What changed
- New `delivery_api.db_pool`: ONE memoized `psycopg_pool.ConnectionPool` per
  DSN (`PROCESS_POOL_MAX=10`, min 0, idle TTL 240s, 30s checkout timeout).
  `pool_for(dsn)` is the single process-wide entry point; `close_all()` is for
  tests and shutdown.
- `postgres_scope` keeps its per-transaction identity contract (claims are
  `set_config` per transaction, never conninfo) and its public surface
  (`TimeoutError('connection limit')`, opened/reused stats via a
  `_delivery_pool_new` marker set in the pool's `configure` callback), but all
  pool internals are psycopg_pool now.
- `InProcessInterviewHost` injects the same pool into the engine's
  `TenantRegistryRouter` (requires interview-engine >= da478f0). Standalone
  engine builds its own pool with the same shape - no cross-repo imports.

## Lessons
1. **No fresh-only checkout in psycopg_pool.** The old `_checkout_fresh`
   became a drain loop: drop dead reused connections and retry until a live
   one or a fresh one arrives; a dead FRESH connection still raises
   immediately. Cap the loop at POOL_OPEN_MAX+1 so a poisoned pool cannot
   spin forever.
2. **Pin contracts, not internal counters.** psycopg_pool discards broken
   connections on putconn and refills in the background, so "opened == N"
   assertions from the hand-rolled pool flake. Assert identity, hygiene and
   budget, not bookkeeping.
3. **Tests must isolate the shared pool.** Any test using real connections
   calls `db_pool.close_all()` before/after - the pool is process-global, so
   leftover state leaks between tests otherwise.
4. **Env-pinned arithmetic rots.** The incident guard pinned
   `POOL_OPEN_MAX == 6` for the old two-pool world; the redesign made the
   whole premise (delivery pool + separate engine conns) obsolete. Guards
   should assert the budget invariant (`pool <= pooler limit - headroom`),
   not a magic number detached from the architecture.
5. **Pre-existing failures are evidence, not noise.** 22 sandbox failures
   (missing chromium, no interview_engine module, no vmodel-contracts
   version, ollama/network) reproduce identically on base 33a46fc - verifying
   against a base worktree is what lets "no new failures" be a claim instead
   of a hope.
