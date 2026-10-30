"""Hosted engine config shares delivery's process pool, no extra connections."""
from interview_engine.pg_question_config import PostgresQualityConfig as EngineQualityConfig

class PostgresQualityConfig(EngineQualityConfig):
    def __init__(self,dsn):
        if not isinstance(dsn,str) or not dsn.startswith(('postgres://','postgresql://','host=')):
            raise ValueError('PostgreSQL engine configuration requires a PG DSN')
        from .db_pool import pool_for
        super().__init__(lambda:pool_for(dsn))
        self.dsn=dsn
