# Target mismatch errors must name the actionable non-secret difference

The owner entered an admin connection URL and hit a generic endpoint mismatch.
The guard was correct to stop before connecting across different targets, but the
message made him guess host versus port versus database. It now prints only the
mismatching host/port/database components with admin and app values, never a URL,
username, password or options. Unchanged fields are not repeated. The app URL
should remain untouched; choose the same session-pooler endpoint/port/database
for the admin credential. Direct host and transaction-pooler port are possible
mistakes, not a claim about what he entered. This guard intentionally does not
compare usernames: admin and runtime roles differ. Actual grant target still
comes from fresh app SELECT current_user, never a parsed pooler suffix.

Two tests failed first.13 focused cases including the existing disposable rollout
passed. No live DB query/write, owner-machine operation, Docker, deploy, spend,
hosted CI or full suite was run.
