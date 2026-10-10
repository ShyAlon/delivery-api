import pytest
from bootstrap_database import bootstrap

@pytest.mark.parametrize('dsn',['host=db.example.com dbname=requi_item6_20261010_test','host=127.0.0.1 dbname=production'])
def test_bootstrap_rejects_nonisolated_destinations_before_connection(dsn,tmp_path):
 with pytest.raises(ValueError,match='disposable'):bootstrap(dsn,tmp_path)
