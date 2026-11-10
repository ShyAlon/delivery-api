# The debug ZIP must carry the evidence it is asked about

Date: 2026-11-10

Problem 1: `req debug` ZIPs hold logs and facts, not the admin request-costs view, so the new statement_trace (round trips by
purpose) never reached us. A heavy request (>= 40 round trips) now writes its own `db.statement_trace` line (top 30
purpose|shape rows with counts and ms; no ids or parameters), which the log tail in the ZIP carries.

Problem 2: facts.json said `delivery-api dirty:1` and a bare count cannot say which file. The collector counts untracked files
too (`git status --porcelain`), while the rebuild's own guard ignores them (`--untracked-files=no`), so the count can be an
untracked file that never blocked a rebuild. facts.json and repos.txt now list the changed paths (status code + path, max 20).
