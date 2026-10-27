# A background refresh failure must not make old results look current

Keep the last rendered phase outputs and mark them stale with last-success time
and an explicit read-only retry. Share freshness handling between initial and
background reads; successful matching reads clear the notice, while mismatched
phase/generation responses do not certify freshness. No faster polling, automatic
mutation retries or deleted results. Regression failed before the helper existed.
