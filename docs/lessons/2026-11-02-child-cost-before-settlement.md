# Empty child ledger is not legacy usage

Before the first settled retail contribution, task totals returned None. The cost GET treated this as legacy document accounting and generated cost.json plus document usage projections. On disposable migrated PG one poll took52queries/13transactions and reported zero as complete, despite child accounting potentially pending or uncertain.

A retained child operation or generator manifest identifies the accounting path. Read the child ledger directly, under existing tenant RLS, including pending attempts/compute and exact posted totals. Unknown is not zero. Confirmed partial contributions remain visible as Pending. No document reads/writes or history reconstruction belong on this path. Legacy document sessions keep their existing reader.

The measured empty child GET is now2queries/1transaction. A real fresh paid-child graph held at a fixture provider response verifies unknown/pending before completion and retains unknown after a failed response. No paid network call is used. Query budget, read-only, cross-owner, partial totals, settled totals and browser pending-to-complete guards cover the contract.
