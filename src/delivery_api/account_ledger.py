"""A signed-in customer's own credit balance and provider-call ledger, as plain text. Read-only: SELECT only, no writes.

Only the columns listed here are read. No prompt, answer or provider response text is ever part of it."""
import json
from datetime import datetime,timezone
from decimal import Decimal

RECENT=20


def _credits(units,per):
    return Decimal(int(units))/Decimal(per)


def _when(stamp):
    return datetime.fromtimestamp(float(stamp),timezone.utc).strftime('%Y-%m-%d %H:%M:%SZ')


def _quote(value):
    if isinstance(value,str):
        try:value=json.loads(value)
        except ValueError:return {}
    return value if isinstance(value,dict) else {}


def read(db,owner_id,usd_per_credit):
    """Balance, recent provider attempts, attempt counts by state and credits charged, for this owner."""
    account=db.execute('SELECT balance_units,units_per_credit FROM credit_accounts WHERE owner_id=?',(owner_id,)).fetchone()
    per=int(account['units_per_credit']) if account else 1
    if per<=0:raise ValueError('credit account has an invalid unit scale')
    balance=_credits(account['balance_units'],per) if account else Decimal(0)
    attempts=db.execute('SELECT attempt_id,job_id,state,created_at,quote FROM provider_attempts WHERE owner_id=? ORDER BY created_at DESC LIMIT %d'%RECENT,(owner_id,)).fetchall()
    by_state={r['state']:int(r['n']) for r in db.execute('SELECT state,count(*) AS n FROM provider_attempts WHERE owner_id=? GROUP BY state',(owner_id,)).fetchall()}
    # Row security limits this table to the signed-in owner; the store refuses to start unless every table enforces it.
    charged=db.execute('SELECT count(*) AS n,COALESCE(sum(posted_units),0) AS posted FROM retail_contributions',()).fetchone()
    charged_credits=_credits(charged['posted'],per) if charged else Decimal(0)
    rate=Decimal(str(usd_per_credit))
    rows=[]
    for a in attempts:
        q=_quote(a['quote'])
        rows.append({'time':_when(a['created_at']),'state':a['state'],'provider':q.get('provider'),'model':q.get('model'),
                     'phase':q.get('retail_phase'),'job':str(a['job_id'])[:8]})
    total=sum(by_state.values())
    lines=['Credit balance: %.2f credits ($%.2f)'%(balance,balance*rate),
           'Provider calls recorded for your account: %d%s'%(total,(' ('+', '.join('%s %d'%(k,v) for k,v in sorted(by_state.items()))+')') if by_state else ''),
           'Credits charged so far: %.4f (%d charged entries)'%(charged_credits,int(charged['n']) if charged else 0)]
    if not rows:lines.append('No provider calls are recorded for your account.')
    else:
        lines.append('Most recent calls, newest first (up to %d):'%RECENT)
        lines+=['%s  %-9s %s/%s  %s  job %s'%(r['time'],r['state'],r['provider'] or '?',r['model'] or '?',r['phase'] or '-',r['job']) for r in rows]
    return {'credits':float(round(balance,6)),'usd':float(round(balance*rate,6)),'calls':total,'by_state':by_state,
            'charged_credits':float(round(charged_credits,6)),'recent':rows,'text':'\n'.join(lines)}
