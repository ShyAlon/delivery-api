# Local subprocess progress contracts require explicit policy and binding

Two protocol tests run actual local child scripts with arbitrary temporary
artifact paths and no cloud gateway. Held PG-first selected cloud policy and
correctly refused setup before their progress/gate assertions.

Keep the restricted PG store. Select local execution explicitly in these two
cases and persist each test's actual engine session before running the child.
All original missing-artifact/nonzero-exit/no-gate and cumulative all-phase
progress/artifact/gate-boundary assertions remain. No phase split, assertion
weakening, coverage loss, skip, production edit or default-bootstrap flip.

Actual interview-engine source f829cfb673d9c7ec49e4697708cf063a908e26fe is now
available for the integration case; its earlier missing-module failure was
an environment setup gap. Full protocol + cumulative phase parity + cloud API:
44 passed in 4.89s current; 44 passed in 5.23s held PG-first.
Hosted CI/full suite remain unverified.

Fail-first with actual engine dependency: 2 failed / 5 passed in 0.94s.
