# Animation tests must name their motion mode (2026-10-28)

The PG browser fixture defaults to reduced motion. Migrating an existing
slow-answer test exposed a real fixture mismatch: its unchanged assertion
expects answer-spin,but accessibility CSS correctly returns none with reduced
motion. That test now explicitly selects no-preference motion. No product CSS
or behavior assertion changed. The full16-second wait remains.

Five failure/cancellation/keyboard/shadow-copy cases plus guard,cloud API and
bootstrap passed44,skipped0 in32.41s after the fixture-signature red and motion
mismatch red. _answer_page accepts the unique tenant task ID while preserving
the default for remaining legacy callers. Mocks still bound coverage to browser
behavior,not real engine delivery. Screenshot evidence shows sending/cancel and
preserved shadow text;scrolled sticky headings partly clip upper content,not
claimed fixed. Hosted CI and full-suite remain unverified.
