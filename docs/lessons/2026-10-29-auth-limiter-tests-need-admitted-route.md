# Rate-limit ordering needs an admitted test route (2026-10-29)

Signed PG middleware refuses engine routes503 while cloud execution is closed,
before auth dependencies run. A direct local rate-limit port therefore cannot
reach404/409/429. Set only runtime installed() in the two hermetic dependency
contracts,no engine/provider installed or called. Separately prove closed503
and admitted unsigned401 still hold. Preserve original domain assertions and
restore limiter via monkeypatch.

Guard first1failed0.55s;direct port2failed40passed4.52s;admitted42passed4.50s.
Final auth/cloud/bootstrap54passed0skips6.09s. No production change;local auth
legacy contracts still debt. Hosted CI/full suite unrun.
