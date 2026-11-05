#!/usr/bin/env bash
# Owner Mac Docker-host gate on the reviewed checkout. No hosted credentials.
set -euo pipefail
cd "$(dirname "$0")/../.."
printf '%s\n' 'LOCAL PG GATE: same reviewed app image; fictional owner and isolated owned PostgreSQL only.'
printf '%s\n' 'Step 1/2: build/accept the exact current image with owned Auth/provider fixtures. No live database, account or paid call.'
bash scripts/ops/image.sh --accept-local
printf '%s\n' 'Step 2/2: isolated persistent PG dependency, app UID peer login/RLS, restart persistence, admin-peer refusal and session revocation.'
bash scripts/ops/image.sh --local-pg-check
printf '%s\n' 'PASS: both Docker-host gates completed. This does NOT prove real hosted sign-in, production pooler parity or model quality. No normal preview or task started.'
