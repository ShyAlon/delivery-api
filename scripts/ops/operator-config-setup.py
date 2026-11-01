#!/usr/bin/env python3
"""Owner-run operator read setup: read-only registry, confirmed private file save.

Usage: .venv/bin/python scripts/ops/operator-config-setup.py REPO [--env-file PATH]
Never prints values, grants privileges, enrolls an operator, or writes to DB.
Copies the registry identity/version and only the five Debug read capabilities.
"""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile

ROOT=Path(__file__).resolve().parents[2]

def module(name,path):
    spec=importlib.util.spec_from_file_location(name,path)
    result=importlib.util.module_from_spec(spec);spec.loader.exec_module(result);return result

check=module('requi_operator_check',ROOT/'scripts/ops/operator-config-check.py')

def settings_module():return module('requi_operator_settings',ROOT/'scripts/ops/settings.py')


def from_rows(rows):
    if len(rows)!=1:raise ValueError('exactly one enabled registry row required')
    subject,version,caps=rows[0]
    # Validate the complete DB grant, but copy only the Debug read subset.
    candidate=dict(zip(check.SETTINGS,(str(subject),version,json.dumps(caps))))
    if not all(check.configuration(candidate).values()):raise ValueError('invalid or incomplete registry')
    candidate[check.SETTINGS[2]]=json.dumps(sorted(check.DEBUG_CAPABILITIES),separators=(',',':'))
    return candidate


def read_registry(conn):
    exists=conn.execute("SELECT to_regclass('public.operator_principals') IS NOT NULL").fetchone()[0]
    if not exists:raise ValueError('registry table missing')
    visible=conn.execute("SELECT has_table_privilege(current_user,'public.operator_principals','SELECT') AND NOT row_security_active('public.operator_principals'::regclass)").fetchone()[0]
    if not visible:raise ValueError('registry cannot be inspected')
    return from_rows(conn.execute('SELECT subject,registry_version,capabilities FROM public.operator_principals WHERE enabled LIMIT 2').fetchall())


def same_target(admin,app):
    from psycopg.conninfo import conninfo_to_dict
    left=conninfo_to_dict(admin);right=conninfo_to_dict(app)
    return all(left.get(k,default)==right.get(k,default) for k,default in (('host',''),('port','5432'),('dbname','')))


def save(settings,destination,values,confirm):
    destination=Path(destination)
    settings.read_file(destination)  # refuses symlinks, nonfiles, nonprivate mode
    before=destination.read_bytes() if destination.exists() else None
    if confirm('Save the three operator settings with only the five Debug read permissions to the selected private settings file? Type "yes" to save, anything else to cancel: ')!='yes':return False
    settings.read_file(destination)
    if (destination.read_bytes() if destination.exists() else None)!=before:raise ValueError('settings changed during confirmation')
    destination.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    fd,temporary=tempfile.mkstemp(prefix='.operator-settings-',dir=destination.parent)
    try:
        with os.fdopen(fd,'wb') as stream:
            stream.write(before or b'')
        os.chmod(temporary,0o600)
        from dotenv import set_key
        for name in check.SETTINGS:set_key(temporary,name,values[name],quote_mode='always')
        # Validate once more before replacing the original. No broad settings copy.
        candidate=settings.read_file(temporary)
        if any(candidate.get(k)!=values[k] for k in check.SETTINGS):raise ValueError('save validation failed')
        settings.read_file(destination)
        if (destination.read_bytes() if destination.exists() else None)!=before:raise ValueError('settings changed during save')
        os.replace(temporary,destination)
    finally:
        if os.path.exists(temporary):os.unlink(temporary)
    return True


def run(root,*,env=None,explicit=None,confirm=None,connect=check.read_connection,out=None):
    out=sys.stdout if out is None else out
    report={'settings.loaded':False,'database.targets_match':False,'registry.verified':False,'settings.saved':False}
    try:
        settings=settings_module();values=settings.load(Path(root),env=env,explicit=explicit)
        report['settings.loaded']=True
        if not values.get('REQUI_ADMIN_DATABASE_URL') or not values.get('DELIVERY_DATABASE_URL'):raise ValueError('both DSNs required')
        if not same_target(values['REQUI_ADMIN_DATABASE_URL'],values['DELIVERY_DATABASE_URL']):raise ValueError('targets differ')
        report['database.targets_match']=True
        with connect(values['REQUI_ADMIN_DATABASE_URL']) as conn:operator=read_registry(conn)
        report['registry.verified']=True
        destination=Path(explicit).expanduser() if explicit else settings.target_path(env)
        # Private target must not select a different project on next file-first load.
        existing=settings.read_file(destination)
        for name in settings.IDENTITY:
            if existing.get(name) and existing[name]!=values.get(name):raise ValueError('target identity conflict')
        report['settings.saved']=save(settings,destination,operator,confirm or settings.terminal_confirm)
    except (Exception,KeyboardInterrupt):
        report['setup.completed']=False
    else:report['setup.completed']=report['settings.saved']
    for key,value in report.items():print(key+'='+str(bool(value)).lower(),file=out)
    return 0 if report['settings.saved'] else 1


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('repo',type=Path);parser.add_argument('--env-file')
    args=parser.parse_args();return run(args.repo,explicit=args.env_file)

if __name__=='__main__':sys.exit(main())
