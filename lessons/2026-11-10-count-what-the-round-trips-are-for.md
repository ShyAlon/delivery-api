# 2026-11-10: count what the round trips are for, not only how many

**Symptom.** A warm answer ran 323-374 statements in about 104-117 s and the debug ZIP could only name the slowest statement per request, so nobody could say what the rest were for.

**Cause of the blind spot.** `db_queries` counted statements run through `_execute_native` and the pipelined setup. It did not count pool checks, SAVEPOINTs or commits, and nothing grouped statements by caller.

**Fix.** `statement_trace` keeps a bounded rolling count per (request type, calling purpose, statement shape) including the overhead, and it rides the existing request-cost view into the debug ZIP (`statement_trace` key). Shapes are literal-free, purposes are function names, request types are route templates.

**Rule.** Before cutting queries, instrument the real path and group by purpose. A sandbox trace of one warm answer showed 26% real statements and 74% overhead; counts from `db_queries` alone would have hidden that.
