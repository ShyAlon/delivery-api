#!/usr/bin/env python3
"""Prepare private paid-continuation review. Does not authorize or execute it."""
import argparse,json
from pathlib import Path
from recovery_manifest import prepare_review
from isolation import RunIsolation
p=argparse.ArgumentParser()
p.add_argument('--dump',required=True,type=Path);p.add_argument('--source-run',required=True,type=Path)
p.add_argument('--runtime',required=True,type=Path);p.add_argument('--output',required=True,type=Path)
p.add_argument('--maximum-expense-units',required=True,type=int)
a=p.parse_args()
review,digest=prepare_review(run_id='4eda39c0684945ff849b582c60dd91f8',dump=a.dump,source_run=a.source_run,runtime=a.runtime,maximum_expense_units=a.maximum_expense_units)
RunIsolation.write(a.output,review)
print(json.dumps({'kind':'item6-paid-continuation-review','review':review,'review_sha256':digest,'network_started':False,'launch_authorized':False},indent=2))
