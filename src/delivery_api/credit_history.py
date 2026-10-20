"""A signed-in customer's own credit history, read-only: every recorded change to their balance.

Reads the credit_ledger table. If the table is not there yet (the SQL has not been applied), it says so
and the balance header keeps working. Row security limits the rows to the signed-in owner. Credits only."""
from datetime import datetime,timezone
from decimal import Decimal

LIMIT=100
COLUMNS='entry_id,kind,delta_units,balance_before_units,balance_after_units,units_per_credit,before_units_per_credit,actor,reason,reference,recorded_at'
LABELS={'topup':'Top-up','grant':'Credits added','debit':'Used','adjustment':'Adjustment','rescale':'Unit change (no credits moved)','unattributed':'UNATTRIBUTED change'}


def available(db):
    row=db.execute("SELECT to_regclass('public.credit_ledger') IS NOT NULL AS present",()).fetchone()
    return bool(row and row['present'])


def _credits(units,per):
    return float(Decimal(int(units))/Decimal(int(per)))


def _when(stamp):
    return datetime.fromtimestamp(float(stamp),timezone.utc).strftime('%Y-%m-%d %H:%M:%SZ')


def _who(actor):
    if actor.startswith('unattributed:'):return 'not recorded (database role %s)'%actor.split(':',1)[1]
    if actor.startswith('system:'):return 'automatic'
    return 'Requi.re operator'


def _entry(r):
    per=r['units_per_credit']
    return {'id':int(r['entry_id']),'at':float(r['recorded_at']),'time':_when(r['recorded_at']),'kind':r['kind'],'label':LABELS[r['kind']],
        'credits':_credits(r['delta_units'],per),'balance_before':_credits(r['balance_before_units'],r['before_units_per_credit']),
        'balance_after':_credits(r['balance_after_units'],per),'by':_who(r['actor']),'reason':r['reason'],'reference':r['reference'],
        'unattributed':r['kind']=='unattributed'}


def last_topup(db):
    """The latest time credits were added (a top-up or a grant), or None."""
    if not available(db):return None
    r=db.execute("SELECT %s FROM credit_ledger WHERE kind IN ('topup','grant') AND delta_units>0 ORDER BY entry_id DESC LIMIT 1"%COLUMNS,()).fetchone()
    if not r:return None
    e=_entry(r);return {'credits':e['credits'],'at':e['at'],'kind':e['kind']}


def read(db,limit=LIMIT):
    if not available(db):
        return {'available':False,'entries':[],'text':'Credit history is not available on this server yet: the credit ledger has not been set up.'}
    rows=db.execute("SELECT %s FROM credit_ledger ORDER BY entry_id DESC LIMIT %d"%(COLUMNS,int(limit)),()).fetchall()
    total=db.execute('SELECT count(*) AS n FROM credit_ledger',()).fetchone()['n']
    entries=[_entry(r) for r in rows]
    lines=['Credit history: %d recorded change%s%s. The history starts at the first recorded change; nothing earlier is listed.'%(total,'' if total==1 else 's',', newest first, showing %d'%len(entries) if total>len(entries) else ', newest first')]
    for e in entries:
        s='%s  %-30s %+.6f credits  balance %.6f -> %.6f  by %s'%(e['time'],e['label'],e['credits'],e['balance_before'],e['balance_after'],e['by'])
        if e['reason']:s+='  (%s)'%e['reason']
        if e['reference']:s+='  ref %s'%e['reference']
        lines.append(s)
    if not entries:lines.append('No changes recorded yet.')
    if any(e['unattributed'] for e in entries):lines.append('UNATTRIBUTED means a balance changed without a recorded reason. Copy this section and send it.')
    return {'available':True,'entries':entries,'text':'\n'.join(lines)}
