# Phase claims need row-locked read/check/write, 2026-10-28

The inherited next-phase claim checks state then writes. Two PG transactions can
both pass without SQLite's single-connection lock. The real concurrent regression
failed before the port. Lock the owned task row before phase/gate/reconciliation
read/check/write;one claim wins,one returnsFalse,and one audit event remains.
Keep the owner operation guard and all downstream/predecessor checks. Gate audit
failure must roll back phase state and gate revision. A native port cannot reopen
unsupported tenant rerun or change immutable session/schema bindings.
