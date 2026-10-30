"""PG metrics are the only persistent implementation, never a fallback tier."""
from pathlib import Path
import ast
import pytest


def test_metrics_has_no_file_database_implementation():
    from delivery_api import run_metrics as metrics
    tree=ast.parse(Path(metrics.__file__).read_text())
    assert not hasattr(metrics,'_open')
    assert not hasattr(metrics,'SCHEMA')
    assert not hasattr(metrics,'db_path')
    assert not any(isinstance(n,ast.Import) and any(a.name=='sqlite3' for a in n.names) for n in tree.body)

@pytest.mark.parametrize('dsn,env',[
    ('sqlite:////tmp/old-metrics.db',{}),
    ('postgresql://fake/metrics',{'DELIVERY_METRICS_BACKEND':'sqlite'}),
])
def test_unsupported_metrics_profiles_fail_before_open(dsn,env):
    from delivery_api.run_metrics import selected_metrics
    with pytest.raises(ValueError,match='PostgreSQL'):selected_metrics(dsn,env)
