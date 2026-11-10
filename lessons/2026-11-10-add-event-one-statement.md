# add_event is one statement

Date: 2026-11-10

Problem: `add_event` ran INSERT events then UPDATE tasks.updated_at: two round trips on every progress event, journal page
and answer step (~0.2 s each on a ~164 ms link). Now one data-modifying CTE does both and returns the event id; an unknown
task or another tenant still fails (tests/test_add_event_single_statement.py, count test failed first 2 != 1).

Test seams that named the old second statement were moved to the merged statement
(test_progress_commit_boundary rollback injection; the lock-wait probe in test_postgres_store now matches the text anywhere in
the query). A seam keyed to a statement's first words silently stops testing when the statement is merged: grep for them.
