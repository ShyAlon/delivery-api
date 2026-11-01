#!/usr/bin/env bash
# Requi.re operator onboarding (batch), run as: ops grant-credits: gives a list of signed-up users free credits and the standard admission row (3 concurrent runs, no spend cap, balance only), and sets YOUR own admission row to the $2.00 rolling 24-hour hard limit. Reads and writes the production Supabase database with the admin connection string you type at a hidden prompt (never shown, never stored, never sent anywhere). Shows the whole plan first; nothing is written until you type yes once. Safe to re-run: a grant has a per-person-per-day key, so the same grant is never applied twice in one day.
set -euo pipefail
OPS_NOTE="Nothing was written to the database."
. "$(dirname "$0")/lib.sh"; ops_start grant-credits "$0" --venv; ops_settings REQUI_ADMIN_DATABASE_URL; REPO=$PWD; PY=$REPO/.venv/bin/python
if ! "$PY" -c 'import psycopg' 2>/dev/null; then
  echo "The Python package psycopg (Postgres driver) is missing in $REPO/.venv. This will run:  $PY -m pip install 'psycopg[binary]>=3.2,<4'"
  printf 'Type "yes" to install it, anything else to stop: '; read -r A < /dev/tty || A=no
  [ "$A" = yes ] || abort NOT-CONFIRMED "you did not type yes" "$PY -m pip install 'psycopg[binary]>=3.2,<4'"
  "$PY" -m pip install -q 'psycopg[binary]>=3.2,<4' || abort PIP "pip install failed (error above)" "$PY -m pip install 'psycopg[binary]>=3.2,<4'"
fi
IN=$(mktemp); chmod 600 "$IN"; trap 'rm -f "$IN"' EXIT
printf 'Your own sign-in email (the account that gets the $2.00 / 24h limit): '; read -r OWN < /dev/tty
[ -n "$OWN" ] || abort NO-EMAIL "no email typed" "run ops grant-credits again and type your sign-in email"
printf 'Free credits per person when a line has no number [50]: '; read -r DEF < /dev/tty; DEF=${DEF:-50}
echo "Paste one person per line:  email   or   email credits   (blank line when done; leave empty to only set your own limit)"
echo "OWN=$OWN" > "$IN"; echo "DEFAULT=$DEF" >> "$IN"
while IFS= read -r L < /dev/tty; do [ -n "$L" ] || break; echo "USER=$L" >> "$IN"; done
CONN=$REQUI_ADMIN_DATABASE_URL
REQ_CONN=$CONN REQ_INPUT=$IN "$PY" - <<'PYE'
import datetime,decimal,hashlib,json,os,sys,time,uuid
import psycopg
D=decimal.Decimal
UNITS=10**9              # stored units per credit (matches the product)
OWN_LIMIT_USD_UNITS=2*10**12   # $2.00 in 1e-12 dollars
MAX_PER_PERSON=D(100);MAX_TOTAL=D(500)
def die(code,msg,fix):
    print('\nABORTED [%s]: %s\nNothing was written to the database.\nFix: %s\nThen run: ops grant-credits'%(code,msg,fix));sys.exit(1)
def fmt(d):
    s=format(d,'f')
    return s.rstrip('0').rstrip('.') if '.' in s else s
def ask(prompt):
    # This script's own text arrives on stdin, so a question has to read the terminal itself.
    line=''
    try:
        with open('/dev/tty') as tty:
            sys.stdout.write(prompt);sys.stdout.flush();line=tty.readline()
    except OSError:line=''
    if line=='':die('NO-TERMINAL','could not read your answer: this is not running in a terminal window','run ops grant-credits from a normal terminal window, not through a pipe or redirect')
    return line.strip()
lines=[l.rstrip('\n') for l in open(os.environ['REQ_INPUT'])]
own=[l[4:] for l in lines if l.startswith('OWN=')][0].strip().lower()
try:default=D([l[8:] for l in lines if l.startswith('DEFAULT=')][0].strip())
except Exception:die('DEFAULT','the default credits is not a number','run ops grant-credits again and type a number such as 50')
people=[]
for l in lines:
    if not l.startswith('USER='):continue
    parts=l[5:].replace(',',' ').split()
    if not parts:continue
    email=parts[0].lower()
    try:credits=D(parts[1]) if len(parts)>1 else default
    except Exception:die('LINE','cannot read the credits in: '+l[5:],'use "email" or "email 50"')
    if credits<=0 or credits>MAX_PER_PERSON:die('LIMIT','%s: credits must be above 0 and at most %s'%(email,MAX_PER_PERSON),'fix the line and run ops grant-credits again')
    if '@' not in email:die('LINE','not an email: '+email,'fix the line and run ops grant-credits again')
    people.append((email,credits))
if len({e for e,_ in people})!=len(people):die('DUP','the same email appears twice','remove the duplicate line')
if sum(c for _,c in people)>MAX_TOTAL:die('LIMIT','total above %s credits in one batch (typo guard)'%MAX_TOTAL,'split the batch or lower the amounts')
try:conn=psycopg.connect(os.environ['REQ_CONN'],connect_timeout=15)
except Exception as e:die('CONNECT','cannot connect: '+type(e).__name__,'check the connection string (Supabase, Connect, the postgres role; try the Session pooler URI) and run ops grant-credits again')
cur=conn.cursor()
for t in ('credit_accounts','admission_limits','credit_adjustments','operator_principals'):
    cur.execute("SELECT to_regclass(%s)",('public.'+t,))
    if cur.fetchone()[0] is None:die('SCHEMA','table public.%s does not exist'%t,'the database is missing migrations; send me this output')
cur.execute('SELECT id FROM auth.users WHERE lower(email)=%s',(own,));r=cur.fetchone()
if not r:die('OWN-NOT-FOUND','no account with your email %s'%own,'sign in once to the service with that email, then run ops grant-credits')
op=r[0]
cur.execute('SELECT registry_version FROM operator_principals WHERE enabled LIMIT 1');r=cur.fetchone()
reg=r[0] if r else 'grant-script-1'
today=datetime.date.today().strftime('%Y%m%d')
plan=[];skipped=[]
def info(email):
    cur.execute('SELECT id FROM auth.users WHERE lower(email)=%s',(email,));r=cur.fetchone()
    if not r:return None
    oid=r[0]
    cur.execute('SELECT balance_units,units_per_credit,updated_at FROM credit_accounts WHERE owner_id=%s',(oid,));acc=cur.fetchone()
    cur.execute('SELECT concurrency_max,expense_limit,period_seconds FROM admission_limits WHERE owner_id=%s',(oid,));lim=cur.fetchone()
    return oid,acc,lim
everyone=[(own,None)]+[(e,c) for e,c in people if e!=own]
if own in [e for e,_ in people]:everyone=[(own,dict(people)[own])]+[(e,c) for e,c in people if e!=own]
for email,credits in everyone:
    i=info(email)
    if i is None:skipped.append((email,'no account yet: they must sign up first, then run ops grant-credits again'));continue
    oid,acc,lim=i
    if acc and acc[1]!=UNITS:skipped.append((email,'credit account uses %s units per credit, not %s'%(acc[1],UNITS)));continue
    key=None;already=False
    if credits is not None:
        key='grant-%s-%s-%s'%(oid,credits.normalize(),today)
        cur.execute('SELECT 1 FROM credit_adjustments WHERE request_key=%s',(key,));already=cur.fetchone() is not None
    limit_action=None
    if email==own:
        # A completed modern transition must not be undone by onboarding.
        cur.execute("SELECT to_regclass('public.own_test_expense_profiles')");has_profile_table=cur.fetchone()[0] is not None
        modern=None
        if has_profile_table:
            cur.execute('SELECT limit_units,units_per_usd FROM own_test_expense_profiles WHERE owner_id=%s',(oid,));modern=cur.fetchone()
        if modern:
            if modern!=(OWN_LIMIT_USD_UNITS,10**12) or lim is None or lim[1] is not None:
                die('OWN-PROFILE','modern own-test profile or legacy admission row conflicts','review own-test-profile output; never overwrite the profile automatically')
        elif lim is None:limit_action='create own row with $2.00 / 24h limit'
        elif not modern and (lim[1]!=OWN_LIMIT_USD_UNITS or lim[2]!=86400):limit_action='change own row to $2.00 / 24h (was %s)'%('no cap' if lim[1] is None else '%s units / %ss'%(lim[1],lim[2]))
    elif lim is None:limit_action='create standard row (3 concurrent, no cap)'
    plan.append(dict(email=email,oid=oid,acc=acc,credits=None if (credits is None or already) else credits,already=already,key=key,limit_action=limit_action,own=(email==own)))
if not plan or all(p['credits'] is None and not p['limit_action'] for p in plan):
    print('Nothing to change.')
    for e,why in skipped:print('  skipped %s: %s'%(e,why))
    for p in plan:
        if p['already']:print('  already granted today: %s'%p['email'])
    print('You are done here.');sys.exit(0)
print('\nPlan (nothing is written yet). Operator for the evidence rows: %s (registry %s)'%(own,reg))
print('%-34s %-10s %-9s %-9s %s'%('email','balance now','grant','after','admission row'))
tot=D(0)
for p in plan:
    bal=D(p['acc'][0])/UNITS if p['acc'] else D(0)
    g=p['credits'] or D(0);tot+=g
    print('%-34s %-10s %-9s %-9s %s'%(p['email'][:34],fmt(bal) if p['acc'] else 'new',('+%s'%fmt(g)) if g else ('already today' if p['already'] else '-'),fmt(bal+g),p['limit_action'] or 'ok as is'))
for e,why in skipped:print('  skipped %s: %s'%(e,why))
print('Total credits granted: %s'%fmt(tot))
if ask('\nType "yes" (English keyboard) to write all of this in one transaction, anything else stops: ')!='yes':
    conn.rollback();die('DECLINED','you did not type yes','nothing needed')
now=time.time()
try:
    for p in plan:
        oid=p['oid']
        cur.execute('INSERT INTO credit_accounts(owner_id,balance_units,units_per_credit,updated_at) VALUES(%s,0,%s,%s) ON CONFLICT (owner_id) DO NOTHING',(oid,UNITS,now))
        cur.execute('SELECT balance_units,updated_at FROM credit_accounts WHERE owner_id=%s FOR UPDATE',(oid,));before,upd=cur.fetchone()
        if p['credits']:
            delta=int((p['credits']*UNITS).to_integral_value())
            digest=hashlib.sha256(json.dumps({'owner':str(oid),'credits':str(p['credits']),'day':today,'operator':str(op)},sort_keys=True).encode()).hexdigest()
            cur.execute('INSERT INTO credit_adjustments(adjustment_id,request_key,owner_id,operator_subject,registry_version,review_digest,approval_reference,reason,delta_units,units_per_credit,before_balance_units,after_balance_units,expected_account_updated_at,applied_at) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)',
                (uuid.uuid4(),p['key'],oid,op,reg,digest,'operator typed yes in the onboarding script at '+datetime.datetime.now().isoformat(timespec='seconds'),'free launch credits',delta,UNITS,before,before+delta,upd,now))
            cur.execute("SELECT set_config('requi.ledger_kind','grant',true),set_config('requi.ledger_actor',%s,true),set_config('requi.ledger_reason','free launch credits',true),set_config('requi.ledger_reference',%s,true)",('operator:'+str(op),p['key']))
            cur.execute('UPDATE credit_accounts SET balance_units=balance_units+%s,updated_at=%s WHERE owner_id=%s',(delta,now,oid))
        if p['limit_action']:
            if p['own']:
                cur.execute('INSERT INTO admission_limits(owner_id,config_version,period_seconds,rate_window_seconds,concurrency_max,expense_limit) VALUES(%s,%s,86400,60,3,%s) ON CONFLICT (owner_id) DO UPDATE SET expense_limit=EXCLUDED.expense_limit,period_seconds=86400',(oid,reg,OWN_LIMIT_USD_UNITS))
            else:
                cur.execute('INSERT INTO admission_limits(owner_id,config_version,period_seconds,rate_window_seconds,concurrency_max) VALUES(%s,%s,86400,60,3) ON CONFLICT (owner_id) DO NOTHING',(oid,reg))
    conn.commit()
except Exception as e:
    conn.rollback();die('WRITE','the write failed and was rolled back: %s: %s'%(type(e).__name__,str(e)[:300]),'send me this output')
print('\nWritten. Balances now:')
for p in plan:
    cur.execute('SELECT balance_units FROM credit_accounts WHERE owner_id=%s',(p['oid'],));b=cur.fetchone()
    cur.execute('SELECT concurrency_max,expense_limit FROM admission_limits WHERE owner_id=%s',(p['oid'],));l=cur.fetchone()
    print('  %-34s %s credits, admission row %s'%(p['email'][:34],fmt(D(b[0])/UNITS) if b else '-','yes (3 concurrent%s)'%(', $2.00/24h' if l and l[1]==OWN_LIMIT_USD_UNITS else '') if l else 'MISSING'))
own_p=[p for p in plan if p['own']]
if own_p:
    cur.execute('SELECT balance_units FROM credit_accounts WHERE owner_id=%s',(own_p[0]['oid'],));ob=cur.fetchone()
    if not ob or ob[0]<=0:print('\nWARNING: your own account (%s) has 0 credits, so you cannot start a run yourself. Run ops grant-credits again and list your own email with a number.'%own)
print('\nDONE.')
PYE
