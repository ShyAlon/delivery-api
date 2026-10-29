# Enabled acceptance tests must follow the selected purchase fixture

History: 7689cfb (Oct16) moved SandboxPurchaseAdapter and AcceptanceChain from
credits-5 / 500 minor / 5000 hermetic units to credits-12 / 1150 minor /
12000 units. The connected tests retained the earlier values.

Fail-first in an owned migrated PG with the exact /tmp socket: both connected
tests fail PaymentRejected('amount or currency mismatch'), 0.56s.

All changed test values, no production edits:
- ProcessorStub capture USD 5.00 -> 11.50.
- hermetic_purchase balance 5000 -> 12000, retaining once-only posting assertion.
- connected graph purchased balance 5000 -> 12000.
- connected graph final balance 5000-debit -> 12000-debit; debit>0 unchanged.

No assertion, guard, timeout, skip or validation was removed. Product offers,
adapter and chain remain unchanged. The new diagnostic runner refuses existing
DB controls or /tmp socket/lock, starts only its own guarded migrated PG, clones
only its own fixture DB, and propagates pytest's exit code. Dependencies and
generator test checkout must be installed; DELIVERY_TEST_SPEC_SOURCE can name
its src directory. pg_harness cleans up the owned server; socket cleanup verified.

After fixture correction: payment chain passes; graph reaches a separate existing
engine KeyError('checkpoint_ns'), 1 passed / 1 failed in 1.57s original current-profile probe.
The PG-first test profile has the same result (same owned runner). No engine edit
is bundled or authorized by this fixture repair. Adjacent contract/PayPal/chain/
isolation tests: 113 passed in .65s current. Hosted CI unverified. No full-green,
bootstrap/default flip, deployment, live DB or real payment claim.
Final checked-in runner: current 1 passed/1 failed in 1.50s; PG-first 1 passed/
1 failed in 1.83s, same checkpoint_ns. Adjacent PG-first113 pass1.00s.
Run pytest in-process to retain pg_harness ownership state; a subprocess makes
the PG-first conftest correctly refuse the parent's server as external.
