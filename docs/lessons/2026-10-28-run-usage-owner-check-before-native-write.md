# Run usage owner check before native write, 2026-10-28

Remaining legacy-shaped run methods still serve status/accounting/health callers,
even while cloud execution endpoints are blocked. Port their behavior,not their
availability. Native usage must reject attributing another owner's UUID before
writing;RLS remains the database boundary too. Keep the existing run cost result
and task artifact references,without reopening legacy execution or local auth.
