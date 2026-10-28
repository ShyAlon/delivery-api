# Port reviews without dropping operation guards, 2026-10-28

Native review/feedback queries still need the existing outer owner lock and task
operation guard. A bridge-poison test must target review tables,not accidentally
force removal of unrelated guard SQL. Keep real guards;prove family queries no
longer use parent/bridge. Preserve immutable review conflict checks,revision-bound
drafts,actor ownership and feedback+audit rollback in the owning transaction.
