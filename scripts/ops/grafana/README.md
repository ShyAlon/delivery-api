# Owner setup: Grafana Cloud email and Slack alerts

Prepared config only. Nothing has been installed, shipped or sent. The owner enters Grafana values locally in his account and on the Docker host, never in chat, git, app settings or a ticket. Use the existing free account only within its current limits; check the account's current ingest/retention/alert quotas before enabling. No paid upgrade is authorized.

## Log shipping

1. In Grafana Cloud, copy the stack's Loki push URL and tenant username. Create a stack-scoped access-policy token with logs:write only. Keep it in a private local file (0600), outside this checkout. Use the exact endpoint from the account, not a guessed domain.
2. Install Grafana Alloy on the same Docker host. The config uses a local Unix Docker socket and filters only requi-delivery and requi-preview-exec. Docker socket access is powerful, even through a read-only filesystem mount: run Alloy as a trusted operator service, not an Internet-facing container, and expose no listener publicly. Persistent Alloy storage preserves its Docker read offsets across restarts.
3. Set REQUI_LOKI_URL, REQUI_LOKI_USERNAME and REQUI_LOKI_TOKEN_FILE in the service's private local environment. TOKEN_FILE is an absolute path to the private token file, readable only by that service. Do not put the token itself in the environment or command line. Copy config.alloy to the operator service's config directory. Validate first: `alloy validate /path/to/config.alloy`. Tested with Alloy v1.21.3. Validation is not proof of delivery.
4. Start the operator service only when ready to disclose these limited operational events to the owner's Grafana stack. The config drops all ordinary server output and sends only the exact bounded REQUI_ALERT JSON schema. No task/owner/session IDs, prompts, responses, credentials, addresses or free-text exception messages are exported. It does not ship the broader Debug logs in this first slice.
5. Confirm an intentionally emitted local synthetic allowlisted signal reaches Explore: `{app="requi",signal="operator_alert"} | json`. This is not evidence a real breach occurred. Then use Grafana's Test button for both notification integrations and verify receipt yourself. Stop if any private content appears. No sender is installed in Requi.re.

## Rules to create in Grafana

Select the stack's Loki data source under Alerting > Alert rules > New alert rule. Use Grafana-managed rules. Set evaluation to 1 minute, pending period 0, and threshold above 0. Suggested queries (5-minute windows):

- Tripwire: `sum(count_over_time({app="requi",signal="operator_alert"} | json | event="tripwire_breach" [5m]))`
- Provider failure: `sum(count_over_time({app="requi",signal="operator_alert"} | json | event="provider_failure" [5m]))`
- Unresolved accounting: `sum(count_over_time({app="requi",signal="operator_alert"} | json | event="unresolved_accounting" [5m]))`

Use no-data=Normal for these sparse event rules. Treat evaluation/data-source errors as alerting errors, not healthy. Group notifications by alertname and choose a repeat interval such as 1 hour, so repeated blocked calls do not send an email every minute. A resolved window means no recent event, not that accounting or a tripwire was repaired. Alerts describe observed events; the ledger remains authoritative. Logging may precede a transaction's final commit, and a process crash may lose a line, so notification receipt is not proof of ledger state and silence is not proof of safety. Configure Alloy/ingest health separately before relying on delivery.

In Notification configuration > Contact points, add Email and Slack integrations (one combined contact point or two routed points). Enter exact recipients and Slack destination yourself, test both, and attach the rules to that route. Credentials stay in Grafana. No SMTP identity, webhook destination or message-sending code is added to Requi.re. Include "New work is stopped or held; inspect operator accounting before retry" in the notification. Never include customer text.

## Retired sender tests and retained evidence

The uninstalled fixed-route sender seam from #574 is deleted. Its seven test functions are retired, not claimed as still running:
- test_delivery_commits_claim_then_records_explicit_result
- test_unknown_acceptance_and_finish_crash_never_resend
- test_retry_budget_tenant_and_commit_boundary
- test_transport_exception_is_unknown_and_payload_cannot_route
- test_request_transaction_cannot_defer_claim_commit
- test_two_delivery_workers_send_only_once
- test_exhausted_or_invalid_evidence_never_reaches_transport

The first function had five parameter cases, so eleven collected test cases are retired. The database outbox/episode evidence, low-level state methods and their tenant/accounting tests are retained to avoid changing durable schema/history in this code-only slice. They are not a Grafana delivery queue and have no sender installed. Existing admission stops are independent of log collection or notification success.

Live Grafana connectivity, token permission, ingest, Slack/email receipt, quotas and operator service installation are unverified and owner-run. Do not call alerting operational until both delivery tests succeed.

Sources checked:
- https://grafana.com/docs/alloy/latest/reference/components/loki/loki.source.docker/
- https://grafana.com/docs/alloy/latest/reference/components/loki/loki.write/
- https://grafana.com/docs/grafana-cloud/observe-and-act/connect-externally-hosted/data-sources/loki/alerting/
- https://grafana.com/docs/grafana-cloud/observe-and-act/alert-and-measure-reliability/alerting/configure-notifications/manage-contact-points/
