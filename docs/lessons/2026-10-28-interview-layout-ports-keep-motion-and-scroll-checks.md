# Interview layout ports keep motion and scroll checks (2026-10-28)

Seven interview-layout/phase-change cases use signed PG without changing
internal scroll bounds,newest-first numbering,sticky heading,normal/reduced
motion or phase-pause/close assertions. Explicitly start animation cases with
normal motion,then retain their reduced-motion assertion. Shared long-history
helper accepts unique task ID and retains its legacy default.

Signature guard failed first. Seven cases plus guard,cloud API and bootstrap
passed46,skipped0 in29.52s. Phase dialog and pinned Live process/history pixels
inspected. Sticky header still clips upper scrolled content;not fixed. Mocks
remain browser contracts,not live execution. Product unchanged;hosted CI/full
suite unverified.
