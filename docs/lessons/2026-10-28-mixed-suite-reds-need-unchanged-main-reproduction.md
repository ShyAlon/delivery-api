# Mixed-suite reds need unchanged-main reproduction

A small fixture port can expose unrelated legacy failures in a broad module.
Run the unchanged file from fresh main with the same environment before calling
those failures baseline. Record both exact failing names and counts. Keep a
focused green slice for the migrated behavior,but never present that as a full
module green or weaken the old assertions to clean the report.

Here both branches fail test_legacy_intent_view_recovers_full_transcript_from_checkpoint
and test_session_cost_uses_linked_task_retail_total. The latter expects a response
without the new evidence metadata. They remain unresolved.
