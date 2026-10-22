#!/usr/bin/env python3
"""Backfill manifest revisions for legacy file-only artifacts; DRY by default.

One owner per run, explicit DSN + artifacts base. Without --apply it only plans and
measures: the printed byte-size distribution is the storage-bound evidence. A local
run measures local fixture files only - no production size claim (--apply writes
manifest revisions + readiness markers for exactly the tasks it measured)."""
import argparse, json, os, sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from delivery_api.artifact_backfill import backfill_artifact_manifests
from delivery_api.postgres_store import PostgresStore
from delivery_api.tenancy import TenantScope


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dsn', default=os.environ.get('DELIVERY_POSTGRES_DSN', ''),
                        help='PostgreSQL DSN (or DELIVERY_POSTGRES_DSN)')
    parser.add_argument('--owner', required=True, help='owner id (tenant) to backfill')
    parser.add_argument('--artifacts-base', required=True, help='base dir holding tenants/<owner>/sessions/...')
    parser.add_argument('--apply', action='store_true', help='publish manifest revisions (default: plan + measure only)')
    args = parser.parse_args()
    if not args.dsn:
        parser.error('a DSN is required (--dsn or DELIVERY_POSTGRES_DSN); no live/default source')
    store = PostgresStore(args.dsn)
    report = backfill_artifact_manifests(store, args.artifacts_base,
                                         TenantScope(args.owner, args.owner), apply=args.apply)
    json.dump(report, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write('\n')


if __name__ == '__main__':
    main()
