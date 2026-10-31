# Code-only paid-child cutover

Owner selected deletion before activation. This branch does not activate paid execution, deploy or change live policy. Default cloud startup now refuses; explicit local child preview remains. The UI unavailable-message work was deferred by the owner to before cloud deployment.

Retire sampled CPU billing (including unknown-as-zero), thread billing, old in-process interview/generator and the parallel PG generator subprocess launcher/recovery. Keep publication coordinator and runner protocol bridge: their off-loop work supervises child sockets and publishes durable bytes, not an in-process model graph. Constructor-level paid refusals remain. Runtime resource metrics remain operator telemetry, never billing.

## Test inventory

Non-meter model-selection, release proof, accepted-settlement replay, protocol bridge, single slot and lost-claim assertions remain. The old default-open startup test is replaced by fail-closed startup, and paid thread metering is replaced by no-work refusal. Separate process publication tests retire with the removed launcher; child publication/crash acceptance covers the retained path. The retired functions below include renamed/replaced tests, not only removed assertions. `test_product_phase_selection_not_env_model_is_the_authority` and `test_exact_retail_subprocess_cannot_omit_compute_meter` moved unchanged in scope to `test_dispatch_gateway_wiring.py`.

- `tests/test_attempt_ledger.py::test_fake_only_runner_descriptor_lifetime_covers_subprocess`
- `tests/test_child_compute.py::test_a_dispatched_child_run_is_charged_its_cpu_also_when_it_fails`
- `tests/test_child_compute.py::test_cpu_charge_posts_once_at_the_unchanged_price_and_replays_without_a_second_debit`
- `tests/test_child_compute.py::test_cpu_seconds_become_milliseconds_and_unknown_is_zero`
- `tests/test_child_compute.py::test_each_run_gets_its_own_segment_key`
- `tests/test_child_compute.py::test_two_runs_of_one_job_are_two_segments_and_a_zero_cpu_run_charges_nothing`
- `tests/test_child_compute.py::test_watchdog_reads_the_cpu_a_real_child_burns_and_an_idle_child_reads_near_zero`
- `tests/test_cloud_runtime.py::test_a_configuration_that_built_once_is_not_rebuilt`
- `tests/test_cloud_runtime.py::test_a_failed_start_installs_nothing_and_keeps_the_reason`
- `tests/test_cloud_runtime.py::test_bad_gateway_configuration_installs_nothing`
- `tests/test_cloud_runtime.py::test_explicit_local_child_selector_refuses_paid_default_before_install`
- `tests/test_cloud_runtime.py::test_install_passes_postgres_database_url_as_engine_session_dsn`
- `tests/test_cloud_runtime.py::test_install_wires_engine_host_and_client_once`
- `tests/test_cloud_runtime.py::test_install_without_postgres_keeps_engine_file_sessions`
- `tests/test_cloud_runtime.py::test_uninstall_removes_everything`
- `tests/test_compute_meter.py::test_founder_exclusion_resumes_after_exception`
- `tests/test_compute_meter.py::test_live_wait_is_not_billable_complete`
- `tests/test_compute_meter.py::test_wait_union_excludes_nested_provider_and_founder_time`
- `tests/test_dispatch_gateway_wiring.py::test_dispatch_denies_closed_when_execution_not_installed`
- `tests/test_dispatch_gateway_wiring.py::test_dispatch_gateway_without_runner_authorization_still_denies`
- `tests/test_dispatch_gateway_wiring.py::test_dispatch_supplies_job_bound_gateway_to_the_runner`
- `tests/test_dual_provider_ledger.py::test_worker_meter_starts_only_for_durable_paid_selection`
- `tests/test_inprocess_engine.py::test_dispatch_runs_generator_and_phase_lifecycle_together`
- `tests/test_inprocess_engine.py::test_exit_codes_reach_the_runner`
- `tests/test_inprocess_engine.py::test_phase_settings_do_not_leak_whether_or_not_the_name_was_already_set`
- `tests/test_inprocess_engine.py::test_process_settings_are_restored_exactly`
- `tests/test_inprocess_engine.py::test_the_decision_handler_is_what_the_generator_sees`
- `tests/test_inprocess_engine.py::test_unexpected_error_is_reported_to_the_runner_and_raised`
- `tests/test_inprocess_interview.py::test_a_failing_step_propagates`
- `tests/test_inprocess_interview.py::test_a_quiet_stop_is_reported_as_stop_requested`
- `tests/test_inprocess_interview.py::test_a_step_is_one_claimed_job_and_returns_the_steps_result`
- `tests/test_inprocess_interview.py::test_an_unclaimed_job_runs_no_step`
- `tests/test_inprocess_interview.py::test_configure_and_current`
- `tests/test_inprocess_interview.py::test_dual_interview_initial_request_and_switch_target_are_pinned_before_metering`
- `tests/test_inprocess_interview.py::test_dual_interview_missing_or_divergent_route_refuses_before_enqueue`
- `tests/test_inprocess_interview.py::test_dual_interview_step_pins_program_route_in_durable_job_options`
- `tests/test_inprocess_interview.py::test_gated_registry_checks_the_stop_marker_after_a_step`
- `tests/test_inprocess_interview.py::test_gated_registry_sends_both_model_steps_through_the_job`
- `tests/test_inprocess_interview.py::test_the_client_is_built_once_and_uses_the_hosted_transport`
- `tests/test_inprocess_interview.py::test_transport_reaches_the_hosted_app_and_carries_the_tenant_scope`
- `tests/test_inprocess_interview.py::test_transport_without_a_scope_is_refused`
- `tests/test_pg_generator_process.py::test_child_runtime_rejects_launch_binding_substitution`
- `tests/test_pg_generator_process.py::test_original_process_documents_reject_revocation`
- `tests/test_pg_generator_process.py::test_process_adapter_only_wraps_fixed_generator_module`
- `tests/test_pg_generator_process.py::test_process_documents_and_recovery_refuse_revoked_auth_session`
- `tests/test_pg_generator_process.py::test_process_launch_pins_original_job_attempt_and_private_descriptor`
- `tests/test_pg_generator_process.py::test_process_launch_refuses_existing_result_without_reproduction`
- `tests/test_pg_generator_process.py::test_process_launch_refuses_unsettled_paid_evidence_without_reproduction`
- `tests/test_pg_generator_process.py::test_process_publication_failure_rolls_back_manifest_result_and_phase`
- `tests/test_pg_generator_process.py::test_process_publication_only_recovery_uses_exact_pg_bytes_without_child`
- `tests/test_pg_generator_process.py::test_process_recovery_publication_failure_preserves_produced_hash_for_retry`
- `tests/test_pg_generator_process.py::test_process_recovery_refuses_changed_authority_or_evidence`
- `tests/test_pg_generator_process.py::test_process_run_recovery_never_spawns_or_changes_provider_ledger`
- `tests/test_pg_generator_process.py::test_published_process_receipt_cannot_mask_a_different_manifest`
- `tests/test_pg_quality_config.py::test_host_real_worker_binding_reads_pg_not_ambient_config_and_resets`
- `tests/test_released_attempts.py::test_gateway_release_passes_the_proof_and_resumes_the_meter`
- `tests/test_released_attempts.py::test_gateway_release_refusal_propagates_and_leaves_the_meter_alone`
- `tests/test_retail_meter_gateway.py::test_changed_source_holds_accepted_usage_and_finishes_meter_pause`
- `tests/test_retail_meter_gateway.py::test_distinct_interview_gateway_uses_same_bound_meter`
- `tests/test_retail_meter_gateway.py::test_exact_retail_subprocess_cannot_omit_compute_meter`
- `tests/test_retail_meter_gateway.py::test_product_phase_selection_not_env_model_is_the_authority`
- `tests/test_run_slots.py::test_process_mode_does_not_use_the_in_process_engine_and_thread_mode_does`
- `tests/test_same_host_producer.py::test_d13_compute_posted_once_excludes_provider_and_founder_waits`

## Validation limits

Bounded validation: 176 runtime/worker/money tests; 41 child/Stop/crash/paid/quality/dispatch/slot tests; 50 postgres-store tests excluding 35 long staging variants; separate actual PG child-control and PG real-architecture variants pass. Whole collection succeeds. The broad postgres-store batch timed out before the long variants finished and is not counted as a pass. No whole-suite, hosted CI, performance or paid full-run claim.
