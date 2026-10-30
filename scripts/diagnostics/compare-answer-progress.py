#!/usr/bin/env python3
"""Compare saved preview terminal logs. Reads local files only, never runs answers."""
import argparse
import re
import json
import sys
from pathlib import Path


def scan(path):
    text=Path(path).read_text()
    answers=[];ingests=[];lags=[]
    for line in text.splitlines():
        fields=dict(re.findall(r"([a-z_]+)='([^']*)'",line))
        if 'http.request' in line and '/answer' in line:
            if fields.get('status')!='200':raise ValueError(f'{path}: failed/non-200 answer present')
            needed=('duration_ms','db_queries','db_ms')
            if not all(k in fields for k in needed):raise ValueError(f'{path}: answer timing fields absent')
            answers.append({k:int(fields[k]) for k in needed})
        if 'progress.ingest.timing' in line:
            ingests.append({k:int(fields[k]) for k in ('events','ingest_ms','fetch_ms') if k in fields})
        if 'loop.lag' in line and 'lag_ms' in fields:lags.append(int(fields['lag_ms']))
    if not answers:raise ValueError(f'{path}: no completed answer request timing found')
    return dict(answers=answers,progress_pages=ingests,max_logged_loop_lag_ms=max(lags) if lags else None,
                loop_lag_note='no log is not proof of zero stall' if not lags else 'maximum within this file, not necessarily this answer')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('before');parser.add_argument('after');args=parser.parse_args()
    try:print(json.dumps(dict(before=scan(args.before),after=scan(args.after)),indent=2))
    except (ValueError,OSError) as exc:print(str(exc),file=sys.stderr);return 1
    return 0

if __name__=='__main__':raise SystemExit(main())
