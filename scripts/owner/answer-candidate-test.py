#!/usr/bin/env python3
"""Owner-run candidate test. No paid provider requests or automatic activation."""
import argparse
from datetime import datetime,timezone
import importlib.util
import os
from pathlib import Path
import socket
import shutil
import subprocess
import sys


class Refused(Exception):pass


def module(path):
    spec=importlib.util.spec_from_file_location('owner_settings',path)
    m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m


def run(command,*,root,env,allowed=(0,)):
    print('RUN: '+' '.join(str(x) for x in command),flush=True)
    result=subprocess.run(list(map(str,command)),cwd=root,env=env)
    if result.returncode not in allowed:raise Refused('command failed with exit '+str(result.returncode)+'; stopped, no automatic retry')
    return result.returncode


def git(root,*args):
    result=subprocess.run(['git','-C',str(root),*args],capture_output=True,text=True)
    if result.returncode:raise Refused('git preflight failed; no changes made')
    return result.stdout.strip()


def database_preflight(dsn):
    import psycopg
    from psycopg.conninfo import conninfo_to_dict
    fields=conninfo_to_dict(dsn);host=fields.get('host','');port=int(fields.get('port','5432'))
    if not host or host.startswith('/') or ',' in host:raise Refused('one explicit TCP database host is required in repo .env')
    try:
        with socket.create_connection((host,port),timeout=5):pass
    except OSError as exc:
        raise Refused('database TCP port '+str(port)+' is unreachable ('+type(exc).__name__+'). University WiFi may block Supabase DB ports. No schema apply or rebuild was run; no SSH/hotspot workaround attempted.') from None
    try:
        with psycopg.connect(dsn,connect_timeout=5,autocommit=True) as conn:
            conn.execute('SET default_transaction_read_only=on');conn.execute('SELECT 1').fetchone()
    except psycopg.Error as exc:
        raise Refused('database TCP connected but read-only login failed ('+type(exc).__name__+'); no values printed, no schema apply or rebuild') from None


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('repo',type=Path);a=p.parse_args(argv)
    root=a.repo.expanduser().resolve();engine=root.parent/'interview-engine'
    for name in ('scripts/ops/req','scripts/owner/answer-proof.py','scripts/owner/apply-pg-cutover.py','tests/test_answer_host_integration.py'):
        if not (root/name).is_file():raise Refused('candidate file missing: '+name+'; do not merge/test an older package')
    if not (root/'.venv/bin/python').is_file():raise Refused('repo .venv/bin/python missing')
    settings=module(root/'scripts/ops/settings.py');values=settings.read_file(root/'.env')
    if not values.get('DELIVERY_DATABASE_URL'):raise Refused('DELIVERY_DATABASE_URL missing from private repo .env; values are never requested in chat')
    # Network is the cheapest known failing input on this WiFi. No git fetch,
    # install, schema write, image build or preview restart precedes it.
    print('PREFLIGHT: read-only DB reachability; repo .env values hidden',flush=True)
    database_preflight(values['DELIVERY_DATABASE_URL'])
    print('PASS: DB TCP and read-only login reachable',flush=True)
    if not values.get('REQUI_ADMIN_DATABASE_URL'):raise Refused('REQUI_ADMIN_DATABASE_URL missing from private repo .env; no schema apply or rebuild')
    for name in ('delivery-api','interview-engine','spec-generator','vmodel-contracts','verification-loop','vmodel-spec-app'):
        checkout=root.parent/name
        if not (checkout/'.git').exists():raise Refused(name+' checkout missing; no schema changes')
        if git(checkout,'branch','--show-current')!='main':raise Refused(checkout.name+' must be on main; no checkout changes made')
        if git(checkout,'status','--porcelain','--untracked-files=no'):raise Refused(checkout.name+' has tracked changes; no automatic stash/reset')
        remote=git(checkout,'remote','get-url','origin')
        if remote.startswith('git@') or remote.startswith('ssh://'):
            raise Refused(checkout.name+' uses SSH origin, which this WiFi blocks on port22. No remote URL was changed. HTTPS Git access must be configured before rebuilding.')
    if not shutil.which('uv'):raise Refused('uv is required for the isolated free proof environment; no schema apply or rebuild')
    for name in ('psycopg','dotenv'):
        try:__import__(name)
        except ImportError:raise Refused('repo test prerequisite missing: '+name+'; no schema apply or rebuild') from None
    env={**os.environ,'REQUI_ROOT':str(root.parent),'PREVIEW_SETTINGS_FILE':str(root/'.env')}
    for command in (['docker','version','--format','{{.Server.Version}}'],['gh','auth','status']):
        result=subprocess.run(command,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,env=env)
        if result.returncode:raise Refused(command[0]+' preflight failed; no schema apply or rebuild')
    # The reviewed owner migration entry reads this same explicit .env. The
    # exact phrase is requested only when check reports pending work (exit3).
    migration=[sys.executable,root/'scripts/owner/apply-pg-cutover.py',root,'--env-file',root/'.env']
    pending=run([*migration,'--check'],root=root,env=env,allowed=(0,3))
    if pending==3:
        # The migration command owns the one exact count/login phrase.
        run([*migration,'--apply','--review-projection-functions','--preview-offer'],root=root,env=env)
        run([*migration,'--check'],root=root,env=env)
    else:print('PASS: no migration pending; no phrase prompt',flush=True)
    run(['bash',root/'scripts/ops/req','rebuild','--apply'],root=root,env=env)
    stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    output=Path.home()/'.local/share/requi/answer-proof'/stamp;output.mkdir(parents=True,mode=0o700)
    # Resolve a separate proof environment, not the owner's runtime venv.
    # The source repos' duplicate wheel URLs are replaced by the one verified
    # delivery wheel for each contract. These are package installs, not models.
    proof=['uv','run','--no-project','--no-sources','--python',root/'.venv/bin/python',
        '--with-editable',root,'--with-editable',engine,'--with','pytest','--with','pgserver','--with','langchain-ollama',
        '--with',root/'vendor/vmodel_contracts-0.28.0-py3-none-any.whl',
        '--with',root/'vendor/requi_child_contract-0.3.0-py3-none-any.whl',
        'python',root/'scripts/owner/answer-proof.py',root,'--output',output]
    proof_env={key:value for key,value in os.environ.items() if key in ('PATH','HOME','LANG','TMPDIR','SYSTEMROOT','SSL_CERT_FILE')}
    code=run(proof,root=root,env=proof_env,allowed=(0,1,2))
    # Always collect the proof table, even when its assertions fail. This read
    # only bundle never triggers paid fallback or changes the recovery spool.
    run(['bash',root/'scripts/ops/req','debug','--run-dir',output],root=root,env=env)
    print(('PASS' if code==0 else 'FAIL')+': candidate cold/warm proof and debug ZIP complete. Fake provider only; no paid calls. Real Docker/rebuild ran on this machine only if its preceding gates passed.')
    return code


if __name__=='__main__':
    try:raise SystemExit(main())
    except (Refused,ValueError,OSError) as exc:
        print('FAIL: '+str(exc),file=sys.stderr);raise SystemExit(2)
