# Removing the last SQLite literal does not finish an API module

Shared app imports still use global bootstrap even when a file has no explicit
SQLiteStore constructor. Port audit/queue receivers to scoped PG but keep the
module marked incomplete until every shared-bootstrap caller is migrated.
Preserve cumulative price,legacy exclusion and human error artifact assertions;
use a raising fixture handler instead of real generation work.
