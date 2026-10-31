# Reserve, journal binding and dispatch are one parent commit

Prepare exact quote/vendor/balance evidence before opening the database transaction. Reserve, bind the durable logical call, recheck live authority and start/prepaid/tripwires in one scoped transaction. The journal must join that exact connection; an independent nested transaction is not atomic. HTTP starts only after commit. Refused tripwire results commit their episode and cancelled evidence, then raise. Unexpected failure rolls back attempt/binding but preserves the incomplete logical marker, so recovery never blind-retries. Refuse a caller's uncommitted outer store operation.

Legacy split reserve/start remains for existing in-process adapters; production parent brokers use the atomic boundary. No profile conversion, arbitrary call-count cap, paid activation or accepted-settlement authority change.
