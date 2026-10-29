# Editable local settings are not cloud configuration coverage (2026-10-29)

Owner Q5 keeps the question-quality editor test local-only. Its mocked GET/PUT
contract tests UI parsing/validation/reset semantics,not engine persistence.
Move exact test AST to dedicated local suite and share existing isolated local
fixtures;remove unused local ui/page bootstrap from shared cloud browser file.
No production capability added,no assertion deleted,no skip/xfail/filter.

Scope guard red1failed0.13s;original1passed3.91s;final45passed0skips15.04s.
Shared fixture/wording slice26passed135deselected3.76s. Editor pixels inspected:
readable defaults and Save/Reset visible. Hosted CI/full suite unrun. Remaining
standalone local-auth subprocesses and local fixture SQLite are explicit debt.
