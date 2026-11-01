# Bound the account page before spend histories

Spend used four correlated history aggregates per account. Materialize the bounded account page first, then use one lateral aggregate per ledger for both windows. Keep recorded_at/settled_at strict cutoffs, fixed-point division and page ordering unchanged. Selecting account.* is unsafe under column-level operator grants: list only the columns already granted.

Disposable PostgreSQL microcheck (100 accounts x 1,000 debit rows, 11-account page, identical fixed-time result, 12 samples): median 30.806ms before, 22.562ms after. No production latency claim. This does not add time indexes or pretend ancient history is free; it removes duplicate traversals and explicitly fences history work behind the page.
