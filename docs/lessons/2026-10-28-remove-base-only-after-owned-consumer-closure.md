# Remove base only after owned consumer closure, 2026-10-28

All PG self method/class-data references resolve to owned definitions or assigned
instance fields. Public protocol methods remain present,including local-auth
methods installed as explicit refusals. A failing independence/registry test first
prevents a deletion that merely drops inherited API or transaction coverage.

Register public operations explicitly,and test that actual public callables equal
the registry plus refused auth names. Never remove SQL adapters merely because the
parent is gone:existing PG-owned operations still use them and must be ported next.
