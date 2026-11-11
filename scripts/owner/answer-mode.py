#!/usr/bin/env python3
"""Owner-only answer accounting selection. Default is a dry review."""
import argparse
import importlib.util
from pathlib import Path
import subprocess
import sys

KEY='DELIVERY_ANSWER_ACCOUNTING_MODE'


def settings_module(root):
    spec=importlib.util.spec_from_file_location('owner_settings',root/'scripts/ops/settings.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module


def schema_ready(dsn):
    import psycopg
    from delivery_api.answer_mode import check_schema
    try:
        with psycopg.connect(dsn,connect_timeout=5) as conn:
            conn.execute('SET TRANSACTION READ ONLY')
            check_schema(conn)
    except psycopg.Error as exc:
        raise ValueError('read-only database check failed ('+type(exc).__name__+'); no settings saved or preview restart') from None



def select(root,mode,apply,*,check=schema_ready,restart=None):
    settings=settings_module(root);values=settings.load(root)
    old=values.get(KEY,'answer-v1')
    print('Answer accounting: '+old+' -> '+mode+'. No answer or provider request is run by this command.',flush=True)
    if not apply:
        print('DRY REVIEW: no settings saved or preview restarted. Use --apply to save and restart.');return
    if mode=='answer-v1':
        if not values.get('DELIVERY_DATABASE_URL'):raise ValueError('DELIVERY_DATABASE_URL missing from shared settings')
        check(values['DELIVERY_DATABASE_URL'])
    print('Only switch when tasks are done or waiting for input and no answer receipt needs reconciliation. No failed answer is retried through legacy.',flush=True)
    # Preserve unrelated private settings using the one atomic settings writer.
    target=settings.target_path()
    settings.save_confirmed(target,{KEY:mode},confirm=lambda _: 'yes')
    loaded=settings.load(root)
    if loaded.get(KEY)!=mode:raise ValueError('saved setting is not effective; preview not restarted')
    env=settings.child_environment(loaded);env['REQUI_ROOT']=str(root.parent)
    env['REQUI_PREVIEW_ENV']=str(target)
    try:
        if restart is None:subprocess.run(['bash',str(root/'scripts/ops/req'),'preview','--apply'],env=env,check=True)
        else:restart(env)
    except Exception:
        raise ValueError('mode saved, preview restart failed; running container may still use previous mode. No retry or rollback. Run req preview --apply after fixing its reported gate.') from None
    print('Mode saved and preview restart completed. Test two new answers; Start and generation keep existing accounting.')


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('repo',type=Path);p.add_argument('--mode',choices=['legacy','answer-v1'],default='answer-v1');p.add_argument('--apply',action='store_true');a=p.parse_args(argv)
    select(a.repo.expanduser().resolve(),a.mode,a.apply)


if __name__=='__main__':
    try:main()
    except (ValueError,OSError,KeyboardInterrupt) as exc:
        print('ANSWER MODE STOPPED: '+(str(exc) if isinstance(exc,ValueError) else type(exc).__name__)+'. Values hidden.',file=sys.stderr);sys.exit(2)
