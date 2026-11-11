"""Default runtime selection with an explicit legacy fallback. No dispatch, schema change or retry."""
KEY='DELIVERY_ANSWER_ACCOUNTING_MODE'


def from_environment(environ,paid_runtime):
    mode=environ.get(KEY,'answer-v1')
    if mode not in ('legacy','answer-v1'):
        raise ValueError(KEY+' must be legacy or answer-v1')
    if mode=='legacy' or paid_runtime is None:return None
    from .child_paid_runtime import PaidChildRuntime
    if not isinstance(paid_runtime,PaidChildRuntime):raise TypeError('typed paid runtime required')
    from .answer_runtime import AnswerAccountingMode
    return AnswerAccountingMode(start_credits='0.1')


SCHEMA_CHECK="SELECT to_regclass('public.answer_charge_receipts') IS NOT NULL AND to_regclass('public.answer_charge_usage') IS NOT NULL AND to_regprocedure('public.requi_answer_start(text,text,text,text,jsonb)') IS NOT NULL AND to_regprocedure('public.requi_answer_finish(text,jsonb)') IS NOT NULL AS ready"


def check_schema(conn):
    row=conn.execute(SCHEMA_CHECK).fetchone()
    ready=row['ready'] if isinstance(row,dict) else row[0]
    if not ready:raise ValueError('answer receipt migration missing; run req migrate before enabling')


def check_installed_schema(dsn):
    # Startup has no tenant identity. Catalog-only read borrows the shared
    # restricted-login pool, never a tenant transaction or private data.
    from .db_pool import pool_for
    with pool_for(dsn).connection() as conn:
        conn.execute('SET TRANSACTION READ ONLY')
        check_schema(conn)
