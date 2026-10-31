# Alert delivery is a durable external-effect boundary

Use an explicitly supplied typed fixed transport, not a recipient chosen by payload text. The helper is not installed or scheduled and creates no SMTP account, sender or recipient. Live use still needs verified communication scope and route/transport readiness.

Commit a claim before transport; reject inherited store/request transactions. Only a positive accepted acknowledgment records sent (transport acceptance, not inbox delivery). A safe transient refusal can retry within the stored budget; exception, unknown acknowledgment, crash or expired budget require operator review. Never blindly resend sending rows or reopen provider admission because alert delivery failed. Feed only validated numeric expense evidence into transport.

Disposable PG tests cover concurrent claim/send-once, tenant boundary, committed claim visibility from another connection, retry exhaustion, unknown acceptance and crash after send before receipt. No email or network side effect.
