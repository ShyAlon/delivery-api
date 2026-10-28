# Denial functions can write

A SELECT calling an expense admission function may persist denial episodes/outbox rows. A native query port must keep write invalidation for those effects, alongside explicit native placeholders. Preserve both owner and global advisory-lock ordering. Verify exact billing with one-time debit replay, rejected next-call admission, rollback to reserved state, and foreign-owner refusal. Running a ledger suite alone green does not close previously observed combined-order failures.
