# Port task SQL with transaction-owned audit, 2026-10-28

A Postgres-owned method is not independent if it still calls a SQLite parent
projection/parser or routes the query through a SQLite dialect bridge. The task
family regression poisons those paths, then exercises actual PG snapshot, rename
collision, artifacts and parsed gates/events. Native SQL keeps the owning scoped
transaction and cache invalidation. A failed audit must roll back the title.

Do not remove the parent or shared adapters until every remaining consumer has
moved. This first family leaves other inherited operations and SQLite runtime intact.
