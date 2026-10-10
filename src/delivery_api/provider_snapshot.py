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
        if not timedelta(0)<=age<=timedelta(hours=24):raise AdmissionDenied('vendor snapshot stale/future; fetch and review current sources')

    def band(self,when):
        t=timestamp(when)
        if t.weekday()>=5:return 'off_peak'
        # Holiday fact is necessary only within a potential weekday peak window.
        potential=1<=t.hour<4 or 6<=t.hour<10
        if not potential:return 'off_peak'
        calendar=self.data.get('holiday_calendar')
        if not isinstance(calendar,dict) or not calendar.get('source') or not calendar.get('sha256') or t.date().isoformat() not in calendar.get('dates',{}):
            raise AdmissionDenied('Chinese holiday calendar authority/date unknown')
        holiday=calendar['dates'][t.date().isoformat()]
        if type(holiday) is not bool:raise AdmissionDenied('holiday fact not verified')
        return 'off_peak' if holiday else 'peak'

    def ceiling(self):
        return {k:max(r[k] for r in self.rates.values()) for k in ('cache_hit','cache_miss','output')}

    def billed_rates(self,start,end):
        start=timestamp(start);end=timestamp(end)
        if end<start or end-start>timedelta(hours=24):raise AdmissionDenied('provider interval missing/outside verified bounds')
        # Entire interval with same rate is safe regardless of unpublished vendor
        # choice of request-acceptance vs generation timestamp. Crossings hold.
        band=self.band(start)
        probe=start.replace(minute=0,second=0,microsecond=0)+timedelta(hours=1)
        while probe<=end:
            if self.band(probe)!=band:raise AdmissionDenied('boundary-straddling billing authority unknown; settlement held')
            probe+=timedelta(hours=1)
        if self.band(end)!=band:raise AdmissionDenied('boundary-straddling billing authority unknown; settlement held')
        return band,self.rates[band]

    def quote_evidence(self,now):
        self.fresh(now)
        return {'snapshot_sha256':self.id,'snapshot':self.data,'admitted_at':timestamp(now).isoformat(),'settlement_rule':'uniform-interval-only-v1'}
