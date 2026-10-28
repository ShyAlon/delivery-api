"""A signed-in customer's own credit balance and provider-call ledger, as plain text. Read-only: SELECT only, no writes.

Only the columns listed here are read. No prompt, answer or provider response text is ever part of it."""
import json
from datetime import datetime,timezone
from decimal import Decimal
from .compute_segments import segment_view

RECENT=20
TIME_COLUMNS=('created_at','posted_at','recorded_at','inserted_at','updated_at','ts')
DEBIT_FETCH=200
SEGMENT_FETCH=50


def _credits(units,per):
    return Decimal(int(units))/Decimal(per)


def _when(stamp):
    return datetime.fromtimestamp(float(stamp),timezone.utc).strftime('%Y-%m-%d %H:%M:%SZ')


def _quote(value):
    if isinstance(value,str):
        try:value=json.loads(value)
        except ValueError:return {}
    return value if isinstance(value,dict) else {}


def _time_of(row):
    for key in TIME_COLUMNS:
        if key in row and row[key] is not None:
            try:return float(row[key])
            except (TypeError,ValueError):return None
    return None


def read(db,owner_id,usd_per_credit,*,marker='?'):
    """Balance, recent provider attempts, attempt counts by state and the credit debits (tokens and compute), for this owner."""
    account=db.execute('SELECT balance_units,units_per_credit FROM credit_accounts WHERE owner_id=%s'%marker,(owner_id,)).fetchone()
    per=int(account['units_per_credit']) if account else 1
    if per<=0:raise ValueError('credit account has an invalid unit scale')
    balance=_credits(account['balance_units'],per) if account else Decimal(0)
    attempts=db.execute('SELECT attempt_id,job_id,state,created_at,quote FROM provider_attempts WHERE owner_id=%s ORDER BY created_at DESC LIMIT %d'%(marker,RECENT),(owner_id,)).fetchall()
    by_state={r['state']:int(r['n']) for r in db.execute('SELECT state,count(*) AS n FROM provider_attempts WHERE owner_id=%s GROUP BY state'%marker,(owner_id,)).fetchall()}
    # Row security limits retail_contributions to the signed-in owner; the store refuses to start unless every table enforces it.
    # Totals come from SQL; the listed entries are the numeric debit rows, reduced to fixed fields below (no prompt or response text lives in this table).
    by_kind={r['kind']:(int(r['n']),_credits(r['posted'],per)) for r in db.execute('SELECT kind,count(*) AS n,COALESCE(sum(posted_units),0) AS posted FROM retail_contributions GROUP BY kind',()).fetchall()}
    raw=db.execute('SELECT * FROM retail_contributions LIMIT %d'%DEBIT_FETCH,()).fetchall()
    rate=Decimal(str(usd_per_credit))
    # Compute records: which ones are not settled, whether they block new work, and whether the owner can release them.
    seg_rows=db.execute('SELECT s.segment_key,s.job_id,s.phase_key,s.state,w.state AS job_state FROM retail_compute_segments s LEFT JOIN worker_jobs w ON w.job_id=s.job_id WHERE s.owner_id=%s ORDER BY s.started_at DESC LIMIT %d'%(marker,SEGMENT_FETCH),(owner_id,)).fetchall()
    per_attempts={str(r['job_id']):int(r['n']) for r in db.execute('SELECT job_id,count(*) AS n FROM provider_attempts WHERE owner_id=%s GROUP BY job_id'%marker,(owner_id,)).fetchall()}
    per_tokens={str(r['job_id']):int(r['n']) for r in db.execute("SELECT job_id,count(*) AS n FROM retail_contributions WHERE kind='tokens' GROUP BY job_id",()).fetchall()}
    per_nonterminal={str(r['job_id']):int(r['n']) for r in db.execute("SELECT job_id,count(*) AS n FROM provider_attempts WHERE owner_id=%s AND state IN ('reserved','dispatched','uncertain','held') GROUP BY job_id"%marker,(owner_id,)).fetchall()}
    segments=[segment_view(dict(r),per_attempts.get(str(r['job_id']),0),per_tokens.get(str(r['job_id']),0),per_nonterminal.get(str(r['job_id']),0)) for r in seg_rows]
    open_segments=[s for s in segments if s['state']!='settled']
    debits=[]
    for d in raw:
        stamp=_time_of(d)
        debits.append({'time':_when(stamp) if stamp is not None else None,'_at':stamp or 0.0,'kind':d['kind'],'phase':d['phase_key'],'job':str(d['job_id'])[:8],'credits':float(round(_credits(d['posted_units'],per),6))})
    debits.sort(key=lambda x:x['_at'],reverse=True)
    debits=debits[:RECENT]
    for d in debits:d.pop('_at')
    rows=[]
    for a in attempts:
        q=_quote(a['quote'])
        rows.append({'time':_when(a['created_at']),'state':a['state'],'provider':q.get('provider'),'model':q.get('model'),
                     'phase':q.get('retail_phase'),'job':str(a['job_id'])[:8]})
    total=sum(by_state.values())
    debit_count=sum(n for n,_ in by_kind.values());debit_credits=sum((c for _,c in by_kind.values()),Decimal(0))
    split=', '.join('%s %.6f'%(k,c) for k,(n,c) in sorted(by_kind.items()))
    lines=['Credit balance: %.4f credits ($%.4f)'%(balance,balance*rate),
           'Provider calls recorded for your account: %d%s'%(total,(' ('+', '.join('%s %d'%(k,v) for k,v in sorted(by_state.items()))+')') if by_state else ''),
           'Credits debited (tokens + compute): %.6f in %d entries%s'%(debit_credits,debit_count,(' ('+split+')') if split else '')]
    if not debits:lines.append('No debits are recorded for your account.')
    else:
        lines.append('Debits, newest first (up to %d):'%RECENT)
        lines+=['%s  %-8s %s  job %s  %.6f credits'%(d['time'] or '(time unknown)',d['kind'],d['phase'] or '-',d['job'],d['credits']) for d in debits]
    if not rows:lines.append('No provider calls are recorded for your account.')
    else:
        lines.append('Most recent provider calls, newest first (up to %d):'%RECENT)
        lines+=['%s  %-9s %s/%s  %s  job %s'%(r['time'],r['state'],r['provider'] or '?',r['model'] or '?',r['phase'] or '-',r['job']) for r in rows]
    blocking=sum(1 for s in open_segments if s['blocks_admission']);can=sum(1 for s in open_segments if s['releasable'])
    if not segments:lines.append('Compute records: none.')
    elif not open_segments:lines.append('Compute records: %d, all settled.'%len(segments))
    else:
        lines.append('Compute records: %d, %d not settled, %d block new work, %d can be released below.'%(len(segments),len(open_segments),blocking,can))
        lines+=['  %-10s job %s  %s  provider calls %d  blocks new work: %s  releasable: %s'%(s['state'],s['job'],s['phase'] or '-',s['provider_calls'],'yes' if s['blocks_admission'] else 'no','yes' if s['releasable'] else 'no') for s in open_segments[:RECENT]]
    return {'segments':open_segments,'credits':float(round(balance,6)),'usd':float(round(balance*rate,6)),'calls':total,'by_state':by_state,
            'charged_credits':float(round(debit_credits,6)),'debits':debits,'recent':rows,'text':'\n'.join(lines)}
