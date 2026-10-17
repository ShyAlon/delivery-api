"""Hermetic checks of the vendor-terms status: warnings are recorded, logged, persistent, and cleared only by a good check."""
import datetime
import pytest
from delivery_api import vendor_status


@pytest.fixture(autouse=True)
def clean():
    vendor_status.reset();yield;vendor_status.reset()


def snap(hours_old):
    return {'model':'m','fetched_at':(datetime.datetime.now(datetime.timezone.utc)-datetime.timedelta(hours=hours_old)).isoformat()}


def test_a_fresh_pin_is_ok_and_an_old_unchecked_pin_is_a_warning():
    vendor_status.pin(snap(1))
    assert vendor_status.status()['ok'] is True
    vendor_status.reset();vendor_status.pin(snap(30))
    got=vendor_status.status()
    assert got['ok'] is False and got['warnings'][0]['kind']=='terms-not-verified' and '30 hours' in got['warnings'][0]['text']
    vendor_status.verified(snap(30))
    assert vendor_status.status()['ok'] is True


def test_a_warning_is_recorded_counted_logged_once_and_cleared_by_a_good_check():
    lines=[]
    vendor_status.pin(snap(1))
    vendor_status.warn('vendor-read','cannot read the pages',log=lines.append)
    vendor_status.warn('vendor-read','cannot read the pages',log=lines.append)
    got=vendor_status.status()
    assert got['ok'] is False and len(got['warnings'])==1 and got['warnings'][0]['count']==2 and got['warnings'][0]['first_seen']
    assert len(lines)==1 and 'WARNING vendor terms (vendor-read): cannot read the pages' in lines[0]
    vendor_status.verified()
    assert vendor_status.status()['ok'] is True and vendor_status.status()['last_verified_at']


def test_never_pinned_means_no_warning_until_a_model_is_known():
    assert vendor_status.status()['ok'] is True
