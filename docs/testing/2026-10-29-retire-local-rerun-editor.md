# Retire local rerun and question-quality editor tests

Decision: 2026-10-29 owner Q4 reverses keep-local: remove the eight local rerun tests and the question-quality editor browser test. No production deletion.

Removed behavioral coverage:
- tests/test_tasks.py: test_rerun_previews_then_archives_with_cause_and_audit, test_archive_policy_marks_downstream_stale_and_keeps_upstream, test_rerun_refuses_stage_with_unfulfilled_prerequisite, test_rerun_proceeds_when_every_upstream_phase_is_done.
- tests/test_phase_output_legacy_rerun.py: test_rerun_does_not_relabel_old_canonical_or_partial_as_current.
- tests/test_local_rerun_browser.py: test_rerun_preview_explains_archive_and_audit_has_local_actor, test_confirm_rerun_button_disables_until_the_server_returns, test_rerun_review_opens_top_dialog_and_escape_restores_flow.
- tests/test_local_question_quality_browser.py: test_debug_dialog_edits_question_quality_settings.

Also removed the two obsolete source-classification guard functions in test_local_rerun_browser_scope.py and test_local_question_quality_scope.py. These are not counted among the nine behavioral tests. The dedicated SQLite browser fixture has no remaining import consumers.

Coverage lost: local rerun preview/archive/audit, upstream prerequisite and stale-state behavior, legacy phase-output preservation across rerun, rerun modal stacking/Escape and in-flight disablement, browser editing of question-quality settings. Production local rerun/archive code and frontend UI remain. Other mocked observability coverage and question-quality API/engine tests remain; this is not a claim that every affected production path is now wholly untested. Cloud rerun refusal and tenant no-button checks remain, as do PG prepare_rerun refusal checks.

Validation:
- Before deletion: nine behavioral tests plus two classification guards passed on untouched main3ecab3fe. Combined baseline including the two failures below:11passed,2failed in11.90s.
- One-off absence assertion failed while dedicated browser file existed, passed after deletion. Verified no imports of the deleted dedicated modules remain.
- Final mixed task/cloud/bootstrap/tenant availability/PG phase gate/question-quality API slice:157passed in20.61s.
- Expanded slice with observability:167passed,2failed in20.57s. Both failures also reproduce against untouched main: test_execute_job_marks_a_failure_record_that_itself_fails has a capability fake missing session_id; test_a_held_prepaid_settlement_is_marked has a fake missing _execute_native for the PG advisory lock. They remain unchanged and require a separate test-double fix.
- No surviving assertion changed or weakened. No production file changed. No full-suite or hosted CI verification claimed.

Lesson: retiring an approved local-only feature's test fixture must remove its classification guards with it, while retaining cloud refusal and security coverage. An expanded slice can expose unrelated stale fakes; reproduce those on the base rather than hiding them or broadening a deletion PR.
