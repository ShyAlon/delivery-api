# Schema binding fixture separates immutable identity from legacy columns

PG binding coverage retains exact schema identity,idempotency and immutable
version refusal. Reopen through an independent PG adapter and prove foreign
owner cannot read or change the binding. Missing/hidden task is KeyError,not an
unbound schema. Keep the SQLite additive-column migration contract explicit
until backend retirement reconciles it;do not erase it as fixture cleanup.
