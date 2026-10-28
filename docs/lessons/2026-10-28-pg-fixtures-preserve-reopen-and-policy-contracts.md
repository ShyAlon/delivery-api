# PG fixture ports must preserve persistence and policy contracts

A store replacement must keep a second independent store when the existing test
checks persistence after reopening. Using the same in-memory fixture object
would hide missing writes. Model-route tests now verify selections through a
second restricted PG store under the same tenant scope, and forbid SQLite opens.

Storage and policy are different seams. Direct execution-detail helper tests keep
their existing local policy mode. A cloud shell has different anonymous probes
from a local shell wrapped in cloud middleware: porting that preview fixture and
loosening its exact-probe assertion would silently change its contract. Park that
separate decision rather than manufacture a passing migration.
