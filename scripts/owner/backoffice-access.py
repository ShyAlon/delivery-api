#!/usr/bin/env python3
"""Owner-reviewed enrollment for exactly the actions the private workspace offers.
Dry by default. Never enables an operator, changes identity/version, adds a role,
allocates credits, starts work, changes a price/policy or writes off an attempt.
"""
import argparse
import importlib.util
import json
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'src'))
from delivery_api.backoffice_access import PURPOSES,DEFAULT_CAPABILITIES,EXCLUDED
from delivery_api.operator_authorization import CAPABILITIES

def module(name,path):
    spec=importlib.util.spec_from_file_location(name,path)
    m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m
setup=module('backoffice_setup',ROOT/'scripts/ops/operator-config-setup.py')


def review(conn,values,lock=False):
    # Registry must already bind the owner. No guessed email, auto-enrollment or
    # identity/version replacement. Lock/recheck after the owner's confirmation.
    rows=conn.execute('SELECT subject,registry_version,capabilities FROM public.operator_principals WHERE enabled'+(' FOR UPDATE' if lock else '')).fetchall()
    operator=setup.from_rows(rows)
    if any(values.get(k)!=operator[k] for k in setup.check.SETTINGS[:2]):raise ValueError('configured identity differs')
    old=setup.check.capabilities(values.get(setup.check.SETTINGS[2]))
    if not old or not old<=set(rows[0][2]):raise ValueError('local authority not covered')
    if not set(rows[0][2])<=CAPABILITIES:raise ValueError('unknown registry capability')
    return operator,old,frozenset(rows[0][2])


def run(root,*,explicit=None,apply=False,confirm=None,out=None):
    out=out or sys.stdout
    report={'review.verified':False,'database.updated':False,'settings.saved':False,'apply.requested':apply}
    try:
        settings=setup.settings_module();values=settings.load(Path(root),explicit=explicit)
        if not setup.same_target(values['REQUI_ADMIN_DATABASE_URL'],values['DELIVERY_DATABASE_URL']):raise ValueError('target mismatch')
        destination=Path(explicit).expanduser() if explicit else settings.target_path()
        settings.read_file(destination);before=destination.read_bytes()
        with setup.check.read_connection(values['REQUI_ADMIN_DATABASE_URL']) as conn:operator,local,registry=review(conn,values)
        report['review.verified']=True
        for cap in PURPOSES:
            report['configured.'+cap]=cap in local;report['registry.'+cap]=cap in registry
            report['requested.'+cap]=True
        for cap in EXCLUDED:report['requested.'+cap]=False
        # Purpose inventory is fixed documented prose; command results are booleans
        # only. The confirmation carries the review, no identity/secret values.
        prompt='Review access for the existing sole operator:\n'+''.join(c+': '+p+'\n' for c,p in PURPOSES.items())
        prompt+='Excluded: credit allocation, suspension, cross-owner mutation, paid activation and write-off. Existing reviewed capabilities are retained.\nType ENROLL REVIEWED BACK OFFICE ACCESS to update this registry row and private settings, or Enter to stop:'
        # The fixed human review is sent to the controlling terminal, while
        # machine-readable stdout remains boolean-only (including dry runs).
        import os
        try:
            fd=os.open(os.environ.get('PREVIEW_TTY','/dev/tty'),os.O_WRONLY|os.O_NOCTTY)
            try:
                if os.isatty(fd):os.write(fd,(prompt.rsplit('Type ENROLL',1)[0]+'Dry review only. No changes.\n' if not apply else '').encode())
            finally:os.close(fd)
        except OSError:pass
        if apply:
            if (confirm or settings.terminal_confirm)(prompt)!='ENROLL REVIEWED BACK OFFICE ACCESS':raise ValueError('not confirmed')
            if destination.read_bytes()!=before:raise ValueError('settings changed')
            settings.read_file(destination)
            import psycopg
            with psycopg.connect(values['REQUI_ADMIN_DATABASE_URL'],connect_timeout=10) as conn:
                conn.execute("SET LOCAL statement_timeout='5s'")
                fresh,fresh_local,fresh_registry=review(conn,values,True)
                if (fresh,fresh_local,fresh_registry)!=(operator,local,registry):raise ValueError('registry changed')
                updated=sorted(registry|DEFAULT_CAPABILITIES)
                conn.execute('UPDATE public.operator_principals SET capabilities=%s WHERE subject=%s AND registry_version=%s AND enabled',
                    (updated,operator[setup.check.SETTINGS[0]],operator[setup.check.SETTINGS[1]]))
            report['database.updated']=True
            operator[setup.check.SETTINGS[2]]=json.dumps(sorted(local|DEFAULT_CAPABILITIES),separators=(',',':'))
            # Already confirmed the exact two effects. The atomic saver still
            # verifies private permissions and concurrent drift at the file boundary.
            if destination.read_bytes()!=before:raise ValueError('settings changed after registry commit')
            report['settings.saved']=setup.save(settings,destination,operator,lambda _: 'yes')
    except (Exception,KeyboardInterrupt):report['operation.completed']=False
    else:report['operation.completed']=not apply or report['settings.saved']
    for key,value in report.items():print(key+'='+str(bool(value)).lower(),file=out)
    return 0 if report.get('operation.completed') else 1

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('repo',type=Path);p.add_argument('--env-file');p.add_argument('--apply',action='store_true')
    args=p.parse_args();sys.exit(run(args.repo,explicit=args.env_file,apply=args.apply))
