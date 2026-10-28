# Scope diagnostic logs by complete fields

An ID substring is not an ownership boundary. A task-a export included task-a-other
because the exporter searched for substrings. Parse quoted/unquoted complete
fields, reject conflicting IDs, malformed records and unowned continuations, and
drop an incomplete first record when reading a byte-limited tail. Bound each
parsed line to avoid expensive parsing of arbitrary long tokens. Keep provider
audit exports blocked for tenant mode until their own privacy contract is approved.
