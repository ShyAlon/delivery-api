# Item5 ops hardening, dry preparation

Health proves only a listener/process. It does not prove current Auth/RLS,
backup/restore, safe paid dispatch, merchant state or full-run readiness.
No public launch, operator grant, provider/account/host access, paid test,
automatic migration, backup upload or restore mutation is authorized here.

## Diagnostics
Activity messages and fields have bounded control-character-safe projection.
Credential-like fields, structured bodies/prompts, URL userinfo/query tokens,
bearer values and obvious inline credential assignments are redacted. Persistent
logs rotate: run5MB with3 backups, each stage2MB with2 backups. Redaction is a
second defense, not permission to log private payloads or a claim arbitrary prose
cannot contain a secret. Keep operational diagnostics metadata-only.

## Offline preflight
scripts/ops-preflight.py reports only check booleans, not env values. Default admin
token, insecure origin/cookies, wrong DB/fallback/provider credentials fail the
fenced profile check. It never connects, grants authority or returns production
ready. Operator identity remains unselected until verified first real signup.
The fixed ops-rollout-checklist.json records live proof still needed at step6.

## Backups and recovery
Both snapshot helpers default dry; execute only against an explicitly approved
source. They create new private directories/files and never overwrite or upload.
- ops-backup.py: local/legacy SQLite snapshot uses SQLite backup API including WAL;
  verifies checksum/size/integrity/FKs. Drill copies into a NEW scratch directory.
  It does not back up cloud data, managed Auth or artifacts.
- ops-postgres-backup.py: explicit owner-only PG service file and named service,
  sanitized child environment, no password/DSN in arguments. pg_dump custom archive,
  pg_restore --list and checksum verification. Raw stderr is not surfaced since
  connection errors may contain credentials. Archive listing is NOT restore proof.
  Managed Auth/config, global roles, object storage and filesystem artifacts are
  separate coverage. Never restore this archive into live Supabase from this tool.

Before launch: configure backups for the actual database and artifact stores,
private encrypted retention/offsite access, age alerting, and restore into a NEW
isolated disposable database with no provider/API traffic. Check row counts,
owner bindings, charge/payment uniqueness and uncertain-work records, not only
pg_restore exit. Record image/schema/source digests and drill evidence. A failed
backup/checksum/drill remains a blocker; never erase uncertainty with an empty DB.
Retention deletion is a separate reviewed operation, not part of these helpers.
No recurring backup/alerts are activated by this branch.

## Host bootstrap and deploy
bootstrap-server.sh defaults dry before privileged commands. Explicit --execute
requires a single reviewed SSH public key, never imports root authorized_keys,
refuses replacing an existing different deploy key and makes /etc/requi private.
Docker deploy access is host-root-equivalent; it is not user/operator authority.
The firewall/packages/Docker/fail2ban remain real host mutations requiring target
approval; these were not run. Delivery deployment stays immutable/loopback/fenced,
retains exact rollback container, with no pruning or automatic credential setup.

## Incident/restart procedure
1. Keep execution/payments fenced. Preserve logs, exact image/schema versions and
   durable jobs/attempts/purchases/events. Never manually zero balances/usage.
2. Validate actual active owner/Auth session and private operator capability.
   Read uncertain attempts/recoverable jobs/payment candidates; no global restart.
3. Reconcile provider usage/payment receipts by exact IDs. Unknown SMTP acceptance,
   provider charge or capture is operator review, not a blind retry/new key.
4. Verify writer/provider quiescence through existing Stop/worker markers before
   cleanup. Handler return, health or elapsed timeout is not quiescence proof.
5. If deploy fails, restore retained exact original container; validate its health
   and data/Auth state. Never infer rollback from a recreated default SQLite launch.
6. Restore data only under explicit approval, into isolated drill first. Apply
   migrations only after verified backup/restore and exact current schema review.

## Evidence boundaries
Unit suites stub external boundaries. Offline SQLite drill uses fixture data only.
Local PostgreSQL archive/restore drill, when performed, is disposable fixture data,
not a managed cloud or production restore certificate. Docker architectures,
TLS/auth/schema/backup retention/outbox/PayPal sandbox/full clean chain remain
step6 evidence. Real-money tests require Shy's explicit ask. Customer checkout UI
remains a separate mock-review task after item5 lands.
