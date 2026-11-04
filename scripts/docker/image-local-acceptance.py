#!/usr/bin/env python3
"""Private local-image acceptance entry, fictional token via stdin only."""
import argparse
import json
from pathlib import Path
import sys
from image_acceptance import API, http_slice
from image_receipt import atomic_write


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url',required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    token=sys.stdin.read(16385).strip()
    if not token or len(token)>16384:raise ValueError('bounded fictional fixture token required on stdin')
    result=http_slice(API(args.base_url,token))
    atomic_write(args.output,json.dumps(result,sort_keys=True).encode())
    print('exact-image HTTP slice passed; retained PG/child proof remains a separate required check')
    return 0


if __name__=='__main__':
    try:raise SystemExit(main())
    except Exception as error:raise SystemExit('Local image acceptance failed ('+type(error).__name__+'); no receipt issued, no state-changing retry.') from None
