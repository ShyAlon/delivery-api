"""Non-bypass, transaction-local request identity for PostgreSQL.

The database credential must be a dedicated restricted application LOGIN that
can SET ROLE requi_backend, but cannot become an owner/service/superuser role.
A Supabase superuser connection string is deliberately rejected.
"""
from contextlib import contextmanager
from .tenancy import current_scope, TenantAccessDenied


class ScopedPostgres:
    def __init__(self, dsn: str):
        import psycopg
        self.dsn = dsn
        # Fail at startup, not after the first user's data is exposed.
        with psycopg.connect(dsn) as connection:
            row = connection.execute('SELECT rolsuper,rolbypassrls FROM pg_roles WHERE rolname=current_user').fetchone()
            if not row or any(row):
                raise TenantAccessDenied('application database role must not bypass RLS')
            unsafe = connection.execute("""
              SELECT 1 FROM pg_roles r
              WHERE (r.rolsuper OR r.rolbypassrls) AND
                    pg_has_role(current_user,r.oid,'MEMBER') LIMIT 1
            """).fetchone()
            if unsafe:
                raise TenantAccessDenied('application role can assume a bypass role')
            connection.execute('SET LOCAL ROLE requi_backend')
            tables = connection.execute("""
              SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
              WHERE n.nspname='public' AND c.relkind='r' AND
                (NOT c.relrowsecurity OR NOT c.relforcerowsecurity OR
                 pg_has_role(session_user,c.relowner,'MEMBER'))
            """).fetchall()
            if tables:
                raise TenantAccessDenied('unprotected table or application schema ownership')

    @contextmanager
    def transaction(self,*,repeatable_read=False):
        import psycopg
        from psycopg.rows import dict_row
        scope = current_scope()
        with psycopg.connect(self.dsn,row_factory=dict_row,options='-c client_encoding=UTF8') as connection:
            with connection.transaction():
                if repeatable_read:connection.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ')
                connection.execute('SET LOCAL ROLE requi_backend')
                connection.execute("SELECT set_config('request.jwt.claim.sub',%s,true)", (scope.owner_id,))
                connection.execute("SELECT set_config('request.jwt.claim.session_id',%s,true)", (scope.session_id or '',))
                yield connection
        # Connection is closed even on failure. No cross-request session identity.
