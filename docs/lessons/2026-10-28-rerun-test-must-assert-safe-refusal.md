# Rerun test must assert safe refusal, 2026-10-28

The reviewed cloud contract refuses tenant rerun preview and confirmation with409
until new-generation checkpoint recovery exists. The old test expected preview200
and pre-admission503. Update only with explicit user approval, preserving the
cross-owner404,unchanged task/session/audit and direct store refusal assertions.
Also assert the refusal code and actionable next step. Never reopen execution to
make a stale test pass.
