"""Real-database checks for the failed-job accounting path. Skipped unless DELIVERY_TEST_POSTGRES_DSN points at an isolated migrated Postgres.
There is no harness that provides one yet, so these do not run in the normal gate."""
import asyncio
import pytest
from test_retail_ledger import account
from test_postgres_store import DSN,A
from delivery_api.tenancy import tenant_context
from delivery_api.retail_pricing import RetailRates
from delivery_api.attempt_ledger import AdmissionDenied
pytestmark=pytest.mark.skipif(not DSN,reason='disposable migrated Postgres required')


def test_failed_job_without_a_provider_call_does_not_block_the_next_job(account):
    s,tid,job=account;r=RetailRates.from_environment({}).snapshot()
    with tenant_context(A):
        s.begin_retail_compute(job,job,'intent',r)
        assert s.close_failed_retail_compute(job)=='closed'
        assert s.retail_compute_status(tid)=={'settled':1}
        assert s.own_ledger(1)['segments']==[]


def test_release_closes_an_unresolved_segment_with_no_provider_call_once(account):
    s,tid,job=account;r=RetailRates.from_environment({}).snapshot()
    with tenant_context(A):
        s.begin_retail_compute(job,job,'intent',r)
        s.finish_retail_compute(job,None)
        s.finish_worker(job)   # a segment is only releasable once its job has stopped running (compute_segments.releasable)
        assert s.retail_compute_status(tid)=={'unresolved':1}
        assert s.own_ledger(1)['segments'][0]['releasable'] is True
        assert s.release_own_compute(False)['released']==0 and s.retail_compute_status(tid)=={'unresolved':1}
        assert s.release_own_compute(True)['released']==1 and s.retail_compute_status(tid)=={'settled':1}
        assert s.release_own_compute(True)['released']==0
