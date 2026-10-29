# Keep credit wiring checks, update only obsolete call shapes

Approved test-only updates; no tests removed. Unchanged account+ledger selection
fails first: two failed / 23 passed in .48s. No product bug established.

Every changed assertion:
- account_balance: replace literal stopBalance() presence with exactly two
  balance.dispose() calls. Keep endpoint, No Session Selected, session price
  formatting, CSS and tooltip text assertions.
- credit_ledger: remove obsolete inline ZIP section tuple 'Credit history',()=>
  from the combined assertion. Keep historyhost markup, identity history API
  fetch and ledger/history/technical-state ordering assertions unchanged. Add
  exact collector source/URL tuple in debug-package.js, its import in identity
  and the actual collectDebugEvidence call wiring.

Correction to earlier diagnosis: history markup is STILL in identity.js, not
in debug-sections.js (which only tracks section availability). The old inline
ZIP collector tuple moved to the shared debug-package collector. Do not encode
a remembered wrong file location in a test.

New executable actual-source checks cover both header routes: disposing prevents
late in-flight balance publication and any new balance read. Collector behavior
keeps successful history payload and marks failed history unavailable, not empty.
32 selected tests pass current1.26s / held PG-first.97s, including existing debug
collector coverage. No assertion intent removed, no skips/timeout changes or
production edits. Hosted CI/full suite unverified; no deploy/default flip.
