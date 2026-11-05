# Local source settings are not a complete runtime

Owner failure on merged677: local-PG start read preview.env through the complete
runtime validator, which required the hosted DB URL the local workflow exists
to replace. Earlier gate bypassed this source-file boundary with fixtures.

Two fail-first tests reproduce missing DB/token and raw ValueError. Split private
value reading, hosted Auth validation and complete runtime validation. Local
source requires only Auth project/public key plus reviewed operator subject;
local DB/token derive from owned resources. Standard preview/deploy still require
the full runtime DB/Auth/token. Invalid source produces a bounded typed owner
failure, no traceback or copied private values. The Docker-host PG gate now runs
that exact auth-only source shape then independently validates derived runtime.
