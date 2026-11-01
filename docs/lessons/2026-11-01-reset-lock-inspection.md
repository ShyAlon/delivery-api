# A failed reset lock needs evidence, not another repair

A stale running job row is data, not a live database lock holder. Inspect actual sessions and their transactions. Reset locks and verifies its final inventory before typed confirmation, then snapshots before deletion. If locks fail, neither confirmation nor snapshot should happen. It holds exclusive locks while waiting for typed input: stop producers first, answer promptly, or cancel. Never bypass locks or terminate sessions automatically.

On lock/statement timeout, the failed connection must unwind before a fresh read-only diagnostic. Include non-self PID, state, application name, wait, query/transaction ages, locks and blocking PIDs. Exclude SQL text and credentials. Current sessions do not prove an expired wait edge; roles may hide details. Bound the diagnostic query and output. The same inspector powers explicit read-only req locks; no typed reset or owner UUID needed.
