# Quality settings forwarding tests are local receivers, not cloud admission

2026-10-29: the four question-quality GET/PUT contracts use unsigned local HTTP
and a mock engine transport. They fail under PG-first identity fencing. Simply
signing them is not correct: cloud GET intentionally hides global configuration
with 404 and PUT needs an administrator. Preserve local forwarding, validation
and engine-unreachable assertions in the existing isolated PG-local subprocess
profile, with real restricted PG and SQLite poison. Do not weaken cloud fences.

Original mock engine token/body assertions and response/config checks retained.
Fail-first held-bootstrap inventory: 4 failed. Migrated: 4 passed current (4.09s);
expanded current and held-bootstrap cloud fence slices recorded in PR. No tests,
assertions, coverage, production code or default bootstrap removed or changed.
Hosted CI/full suite not run. No deploy, external DB, provider calls or spend.
