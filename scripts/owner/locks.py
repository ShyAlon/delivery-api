#!/usr/bin/env python3
"""Read-only current preview DB sessions, bounded and without SQL text/secrets."""
import argparse,importlib.util
from delivery_api.db_lock_diagnostics import report

p=argparse.ArgumentParser(description=__doc__)
p.add_argument('repo');p.add_argument('--env-file');args=p.parse_args()
from pathlib import Path
root=Path(args.repo).expanduser().resolve()
spec=importlib.util.spec_from_file_location('settings',root/'scripts/ops/settings.py')
settings=importlib.util.module_from_spec(spec);spec.loader.exec_module(settings)
report(settings.load(root,explicit=Path(args.env_file) if args.env_file else None)['REQUI_ADMIN_DATABASE_URL'])
