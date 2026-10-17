"""Frozen vendor facts; reserve ceilings, settle only uniform evidenced bands."""
from datetime import datetime,timezone,timedelta
from decimal import Decimal
import hashlib,json
from .attempt_ledger import AdmissionDenied

RULE='Peak hours are 01:00 - 04:00 and 06:00 - 10:00 UTC, Monday through Friday, excluding Chinese public holidays. All other hours are off-peak, including weekends and Chinese public holidays in full.'


def timestamp(value):
    if isinstance(value,str):value=datetime.fromisoformat(value.replace('Z','+00:00'))
    if not isinstance(value,datetime) or value.tzinfo is None:raise AdmissionDenied('aware UTC provider timestamp required')
    return value.astimezone(timezone.utc)


class ProviderSnapshot:
    def __init__(self,snapshot):
        self.data=json.loads(json.dumps(snapshot))
        try:
            if self.data['published_period_rule']!=RULE:raise ValueError()
            self.fetched=timestamp(self.data['fetched_at'])
            if not self.data['model'] or not self.data['model_version']:raise ValueError()
            if len(self.data['sources'])!=2 or any(not s['url'].startswith('https://api-docs.deepseek.com/') or len(s['sha256'])!=64 or any(c not in '0123456789abcdef' for c in s['sha256']) for s in self.data['sources']):raise ValueError()
            self.rates={}
            for band in ('peak','off_peak'):
                rates=self.data['published_usd_per_million'][band]
                if set(rates)!={'cache_hit','cache_miss','output'}:raise ValueError()
                values={k:Decimal(v) for k,v in rates.items()}
                if any(not v.is_finite() or v<0 or v*10**6!=int(v*10**6) for v in values.values()):raise ValueError()
                self.rates[band]={k:int(v*10**6) for k,v in values.items()} # 1e12 USD scale / 1M tokens
            if self.data.get('parser_version')!='deepseek-html-v2':raise ValueError()
        except (KeyError,ValueError,TypeError) as exc:raise AdmissionDenied('vendor snapshot missing/unparseable source, rule, hash or rate') from exc
        self.id=hashlib.sha256(json.dumps(self.data,sort_keys=True).encode()).hexdigest()

    def fresh(self,now):
        age=timestamp(now)-self.fetched
        # Owner ruling 2026-10-17: an old pin is a warning (vendor_status), never a stop; the live page check before each call is the control.
        if age<timedelta(0):raise AdmissionDenied('vendor snapshot is dated in the future; fetch and review current sources')

    def band(self,when):
        t=timestamp(when)
        if t.weekday()>=5:return 'off_peak'
        # Holiday fact is necessary only within a potential weekday peak window.
        potential=1<=t.hour<4 or 6<=t.hour<10
        if not potential:return 'off_peak'
        # Owner ruling 2026-10-17: without a verified holiday fact for the date, bill the peak rate (never below what the vendor can charge).
        calendar=self.data.get('holiday_calendar')
        if isinstance(calendar,dict) and calendar.get('source') and calendar.get('sha256') and isinstance(calendar.get('dates'),dict) and calendar['dates'].get(t.date().isoformat()) is True:
            return 'off_peak'
        return 'peak'

    def ceiling(self):
        return {k:max(r[k] for r in self.rates.values()) for k in ('cache_hit','cache_miss','output')}

    def billed_rates(self,start,end):
        start=timestamp(start);end=timestamp(end)
        if end<start or end-start>timedelta(hours=24):raise AdmissionDenied('provider interval missing/outside verified bounds')
        # A whole interval in one band bills that band. An interval that touches any peak hour bills the peak rate, whichever moment the vendor
        # used (request acceptance or generation), so the cost is never understated.
        bands={self.band(start),self.band(end)}
        probe=start.replace(minute=0,second=0,microsecond=0)+timedelta(hours=1)
        while probe<=end:
            bands.add(self.band(probe));probe+=timedelta(hours=1)
        band='peak' if 'peak' in bands else 'off_peak'
        return band,self.rates[band]

    def quote_evidence(self,now):
        self.fresh(now)
        return {'snapshot_sha256':self.id,'snapshot':self.data,'admitted_at':timestamp(now).isoformat(),'settlement_rule':'uniform-interval-only-v1'}
