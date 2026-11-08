"""Per-request budget digest in the debug export: values as recorded, one row per request."""
def test_request_budgets_digest_carries_the_recorded_per_request_stats():
    from delivery_api.debug_export import request_budgets
    lines = [
        "2026-11-08T06:02:00.025+00:00 | http.request       | Request completed | request_id='e6510848' task_id='7f3b86686274' ref='e6510848' method='POST' path='/api/tasks/7f3b86686274/answer' status='200' duration_ms='118008' db_queries='374' db_ms='100382' engine_calls='0' engine_ms='0' checkout_ms='20306' auth_ms='931' held_ms='0' actor='Signed-In User' client='172.17.0.1'",
        "2026-11-08T06:02:01.000+00:00 | progress.poll.empty | No committed progress; ingestion transaction skipped | request_id='e6510848' task_id='7f3b86686274'",
        "2026-11-08T06:03:00.000+00:00 | http.request       | Request completed | request_id='9' task_id='7f3b86686274' ref='9' method='GET' path='/api/tasks/7f3b86686274' status='200' duration_ms='41'",
    ]
    budgets = request_budgets(lines)
    assert len(budgets) == 2, 'one row per http.request line; poll lines are not requests'
    answer = budgets[0]
    assert answer['method'] == 'POST' and answer['path'].endswith('/answer')
    assert answer['db_queries'] == 374 and isinstance(answer['db_queries'], int)
    assert answer['db_ms'] == 100382 and answer['checkout_ms'] == 20306 and answer['duration_ms'] == 118008
    assert budgets[1]['method'] == 'GET' and 'db_queries' not in budgets[1], 'a request recorded before stats existed keeps only the fields it recorded'
    assert request_budgets(None) == []
