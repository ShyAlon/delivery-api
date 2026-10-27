#!/usr/bin/env bash
# Read-only. Usage: bash requi-check-diagnostics-target.sh /path/to/delivery-api
set -euo pipefail
[ "$#" -eq 1 ] || { echo 'Usage: bash requi-check-diagnostics-target.sh /path/to/delivery-api'; exit 2; }
REPO=$(cd "$1" 2>/dev/null && pwd) || { echo 'ABORT: repo path not found'; exit 2; }
[ -f "$REPO/src/delivery_api/config.py" ] && [ -x "$REPO/.venv/bin/python" ] || { echo 'ABORT: delivery-api checkout and its .venv are required'; exit 2; }
exec "$REPO/.venv/bin/python" - "$REPO" <<'PY'
import getpass,os,pathlib,re,sys
from psycopg.conninfo import conninfo_to_dict
try:
    from dotenv import dotenv_values
except ImportError:raise SystemExit('ABORT: python-dotenv missing from repo .venv')
repo=pathlib.Path(sys.argv[1])

def identifiers(value,effective=None):
    try:
        if effective is None and (value or '').startswith('https://'):
            from urllib.parse import urlsplit
            u=urlsplit(value);info={'host':u.hostname or ''}
        else:info=effective if effective is not None else conninfo_to_dict(value or '')
        host=info.get('host','');user=info.get('user','')
        if not re.fullmatch(r'[A-Za-z0-9.-]{1,253}',host):return '(not a valid URI host)','unknown'
        direct=re.fullmatch(r'db\.([a-z0-9]+)\.supabase\.co',host)
        api=re.fullmatch(r'([a-z0-9]+)\.supabase\.co',host)
        pool=re.fullmatch(r'postgres\.([a-z0-9]+)',user) if host.endswith('.pooler.supabase.com') else None
        return host,(direct or api or pool).group(1) if direct or api or pool else 'unknown'
    except Exception:return '(unparseable connection identifier)','unknown'

def show(label,values):
    print(label)
    for key in ('DELIVERY_DATABASE_URL','DELIVERY_SUPABASE_URL'):
        if values.get(key):
            host,ref=identifiers(values[key]);print(' ',key,'host:',host,'; project ref:',ref)
        else:print(' ',key,': not set')

try:local=dict(dotenv_values(repo/'.env',interpolate=False))
except Exception:raise SystemExit('ABORT: could not read repo .env (contents suppressed)')
# The Python app gives process env precedence over repo .env.
effective={**local,**os.environ}
show('Local Python app candidate (shell environment overrides repo .env):',effective)
settings=pathlib.Path(os.environ.get('PREVIEW_SETTINGS_FILE') or pathlib.Path(os.environ.get('XDG_CONFIG_HOME',str(pathlib.Path.home()/'.config')))/'requi/preview.env')
if settings.is_file():
    try:show('Preview launcher saved settings (separate from local Python app):',dict(dotenv_values(settings,interpolate=False)))
    except Exception:print('Preview settings unreadable; contents suppressed.')
else:print('Preview launcher settings: not found.')
print('These are local configuration candidates, not proof of the running container or server target.')
print('Compare the project ref with the Requi.re project ref shown in your Supabase dashboard. A pooler host alone is shared and cannot identify the project.')
try:answer=getpass.getpass('Optional: type CHECK to inspect the URI used for the migration, or Enter to stop: ')
except EOFError:answer=''
if answer!='CHECK':raise SystemExit(0)
try:import psycopg
except ImportError:raise SystemExit('ABORT: psycopg missing; local identifiers above remain valid.')
uri=getpass.getpass('Paste the SAME migration connection URI here only (hidden): ')
try:conninfo_to_dict(uri)
except Exception:raise SystemExit('ABORT: valid PostgreSQL URI or conninfo required; value not printed.') from None
host,ref=identifiers(uri)
try:
    with psycopg.connect(uri,connect_timeout=10) as conn:
        host,ref=identifiers(uri,{'host':conn.info.host,'user':conn.info.user})
        conn.execute('SET TRANSACTION READ ONLY')
        conn.execute("SET LOCAL statement_timeout='10s'")
        row=conn.execute("SELECT current_database(),to_regclass('public.operator_failure_diagnostics') IS NOT NULL").fetchone()
        print('Entered URI host:',host,'; project ref:',ref)
        print('current_database:',row[0],'; operator_failure_diagnostics exists:',row[1])
        print('Compare this ref with BOTH the app config and dashboard. Matching db name postgres is not enough.')
except Exception as exc:raise SystemExit('CHECK FAILED: '+type(exc).__name__+' (details suppressed; no writes performed).') from None
PY
