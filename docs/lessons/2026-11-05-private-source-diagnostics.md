# Source settings need writer parity and specific safe failures

After678 the owner hit a generic Auth/settings refusal before any dependency
start. That message erased the file path and failing check. It did not prove
whether the real file was missing, permissive, malformed or using quoted values.

The normal owner settings writer uses quoted dotenv. The image reader had kept
literal quote marks, so a valid quoted HTTPS origin failed validation. Five
fail-first regressions prove this class and lost path/check diagnostics. Parse
quoted assignments, optional export and comments without shell/variable
expansion. Keep strict duplicate/name/NUL/size/private-file checks. Report path,
fixed check code and exact repair, never values. Do not infer a different key
name or actual owner failure without a read-only source-shape report.

The Mac gate uses the normal quoted Auth-only source shape. The repo checker
prints path, presence/shape booleans and fixed failing check only. Full runtime
still requires PostgreSQL/Auth/token. No database or hosted account is contacted
by source validation. Errors before setup do not justify cleanup or adding a
hosted DB URL to the local source.
