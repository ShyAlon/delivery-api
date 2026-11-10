# 2026-11-10: the paid call's ledger repeated locks, reads and transactions

## What happened
One paid answer spent 117 syncs / 18 transactions on admission. Measured on the real store through the real broker (fake provider, no prepaid account), one paid dispatch cost 74 syncs / 12 transactions.

## Cause
- The same advisory lock (global, owner) was asked for 2-3 times inside one transaction.
- Authority was read before and after the owner lock; the first read proved nothing the second did not.
- The insert was followed by a read of the row it had just written; transition read the attempt first just to learn its state.
- Journal load and begin were two transactions; the journal row was read back after being written.
- The operator-expense policy was read on a second connection inside the dispatch transaction (pool check, identity, select, commit).
- The ownership check and the raw-usage write were two transactions.

## Change (no migration, exact caps, same fences)
- A transaction-level advisory lock already held by the operation is not asked for again. The set is cleared on a savepoint rollback.
- Authority is read once, after both locks. A test that used the removed read as a sync point now waits for the owner lock; its assertions are unchanged.
- Insert returns the row; transition reports the state it started from.
- `ChildBrokerJournal.claim` (load + begin, one transaction) and `complete` returns the committed row.
- Policy read uses the dispatch connection; `run_grouped` puts the ownership check and raw-usage write in one transaction, committed before the snapshot verification as before.
- Result: one paid dispatch 74 to 53 syncs, 12 to 8 transactions. With prepaid enabled: 80 to 59 syncs, 12 to 8 transactions, including the locked balance check and once-only debit. Admit 25 to 17, settle 11 to 8 on the bare ledger.

## Not changed
Dispatched marker committed before the HTTP send, once-only charge keyed by attempt id, raw response durable before settlement, balance guard locked and exact. Batching independent reads is a separate change.

## Rule
Estimate from the measured statement list, not from the transaction count: most of the cost is statements inside a transaction, which grouping alone does not remove.
