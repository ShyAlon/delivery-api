import pytest
from delivery_api.child_pool_budget import child_mode,pool_budget


def test_default_and_optin_budget_include_compatibility_service():
    assert child_mode({}) is False
    assert pool_budget({})==(9,0,4)
    env={'DELIVERY_CHILD_EXECUTION':'local','DELIVERY_LOCAL_PREVIEW':'1'}
    assert child_mode(env) is True and pool_budget(env)==(8,4,0)
    with pytest.raises(ValueError):child_mode({'DELIVERY_CHILD_EXECUTION':'paid'})
    with pytest.raises(ValueError):child_mode({'DELIVERY_CHILD_EXECUTION':'local'})


def test_real_parent_plus_child_pool_max_and_parked_reads(tmp_path):
    import subprocess,sys,json,os
    script='''import os,json,sys
from concurrent.futures import ThreadPoolExecutor
from delivery_api.db_pool import pool_for,close_all,PROCESS_POOL_MAX
from interview_engine.pg_pool import build_pool
from threading import Barrier
p=pool_for(sys.argv[1]);c=build_pool(sys.argv[1],max_size=4,name='budget-child-simulator')
b=Barrier(13)
def borrow(pool):
 with pool.connection() as conn:
  conn.execute('select 1');b.wait();b.wait()
with ThreadPoolExecutor(max_workers=12) as ex:
 fs=[ex.submit(borrow,p) for _ in range(8)]+[ex.submit(borrow,c) for _ in range(4)]
 b.wait()
 print(json.dumps({'parent_open':p.get_stats()['pool_size'],'child_open':c.get_stats()['pool_size'],'cap':PROCESS_POOL_MAX}),flush=True)
 b.wait()
 for f in fs:f.result()
c.close();close_all()
'''
    # Real disposable migrated PG only. Separate interpreter applies opt-in
    # before db_pool's import-time pool construction.
    from test_postgres_store import DSN
    if not DSN:pytest.skip('disposable Postgres required')
    result=subprocess.run([sys.executable,'-c',script,DSN],capture_output=True,text=True,check=True,
        env={**os.environ,'DELIVERY_CHILD_EXECUTION':'local','DELIVERY_LOCAL_PREVIEW':'1'},timeout=15)
    measured=json.loads(result.stdout.strip())
    assert measured=={'parent_open':8,'child_open':4,'cap':8}
