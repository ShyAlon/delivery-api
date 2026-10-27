# A failed ledger read is not an empty ledger

The Debug live compute line inferred zero blockers from missing ledger data after
its request failed. Track availability separately, reset it when opening Debug,
and only claim zero blockers after a successful read. Failed reads clear release
actions, show unknown and offer a read-only retry. A successful retry refreshes
the live line without reopening Debug. No admission, charge or release semantics
changed. Browser regression was observed failing before the fix and passing
after it, including unavailable-to-empty recovery.
