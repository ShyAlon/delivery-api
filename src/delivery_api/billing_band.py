"""Pure parent-selected band policy preparation, not installed billing.

Reviewed expense and retail rates are separate. An unknown provider timestamp
rule preserves ambiguity; this does not claim exact provider expense.
"""
from dataclasses import dataclass
from datetime import datetime,timezone,timedelta
from .attempt_ledger import AdmissionDenied


@dataclass(frozen=True)
class BandRates:
    peak:int
    off_peak:int

    def __post_init__(self):
        if any(type(x) is not int or x<0 for x in (self.peak,self.off_peak)) or self.peak<self.off_peak:
            raise AdmissionDenied('reviewed fixed-point peak/off-peak rates required')


def peak_at(moment):
    if not isinstance(moment,datetime) or moment.tzinfo is None or moment.utcoffset() is None:
        raise AdmissionDenied('timezone-aware billing timestamp required')
    utc=moment.astimezone(timezone.utc)
    return utc.weekday()<5 and (1<=utc.hour<4 or 6<=utc.hour<10)


def select_rates(*,accepted_at,completed_at,expense,retail,provider_rule='unknown'):
    if not isinstance(expense,BandRates) or not isinstance(retail,BandRates):
        raise AdmissionDenied('expense and retail reviewed bands required')
    first=peak_at(accepted_at);last=peak_at(completed_at)
    if completed_at<accepted_at:raise AdmissionDenied('billing time order mismatch')
    if provider_rule not in ('unknown','acceptance','completion'):
        raise AdmissionDenied('explicit provider band rule required')
    boundary=False
    # Minute-aligned documented windows: inspect every boundary in the call's
    # range, not merely endpoints (a long call can cross out and back).
    day=accepted_at.astimezone(timezone.utc).replace(hour=0,minute=0,second=0,microsecond=0)
    end=completed_at.astimezone(timezone.utc)
    if end-day>timedelta(days=7):raise AdmissionDenied('bounded call interval required')
    while day<=end:
        if day.weekday()<5:
            for hour in (1,4,6,10):
                point=day+timedelta(hours=hour)
                if accepted_at<point<=completed_at:boundary=True
        day+=timedelta(days=1)
    ambiguous=provider_rule=='unknown' and boundary
    selected=first if provider_rule=='acceptance' else last if provider_rule=='completion' else first
    return {'expense_rate':expense.peak if ambiguous or selected else expense.off_peak,
            'retail_rate':retail.off_peak if ambiguous or not selected else retail.peak,
            'billing_band_ambiguous':ambiguous,'provider_band_rule':provider_rule,
            'expense_measurement':'conservative_peak_estimate' if ambiguous else 'reviewed_band_rate',
            'retail_policy':'off_peak_when_ambiguous' if ambiguous else 'reviewed_band_rate',
            'accepted_at':accepted_at.astimezone(timezone.utc).isoformat(),
            'completed_at':completed_at.astimezone(timezone.utc).isoformat()}
