# Full runs on Friday, November 6, 2026

Owner-run sequence. Real payments stay off. No paid run, live migration,
publication or server deployment has been performed for this readiness work.
CI unavailable, local gate only. Merge the full-run handoff PR675, then UI PR674
and the owner-readiness PR before building. Do not run this against an unmerged
checkout. A single Start/answer acceptance is not a full spec run.

## What is proved and what remains

The local fictional-provider browser gate passed interview, needs, SRS,
architecture founder decision, items, tests and validation, alternating two
instances on one disposable PostgreSQL database. This proves the tested
handoffs, not provider quality, real cost, Docker-host performance or current
server readiness. The owner must accept the final merged image once. Settings,
permissions, available credits, provider balance and installed schema remain
checks against the actual selected target.

Allow roughly 30-60 minutes of setup time excluding image downloads/build and
fixes. This is a planning allowance, not measured runtime. Full-run duration and
cost depend on the product and founder answers; the 48-second fixture test is
not a real-run forecast.

## 1. Prepare final sources and exact image (owner, Mac)

Use the usual four sibling clean, tracked main checkouts: delivery-api,
interview-engine, spec-generator, verification-loop. Review `req status`, then
fast-forward each intended source checkout. Do not stash or discard work just
to bypass a refusal. Docker Desktop must support linux/amd64. Existing GitHub
CLI and Docker credentials must access the existing PRIVATE package.

```bash
req status
req image --review
req image --publish ghcr.io/shyalon/delivery-api
```

`req` syncs delivery-api main. It does not sync the other three checkouts for
you. Publication builds/reuses the final sources and runs the owned zero-cost
local acceptance before publishing the SAME image. Do not run a separate
`--accept-local` immediately beforehand: publication already performs it.
Expected: local image accepted, private manifest/config verified, stage 6/6
receipt written. If any stage fails, stop there. No alternate image, server
build or public package. Reuse the same command after fixing the named input;
changed sources select a new keyed run. Keep `accepted-image.json` private.

## 2. Review schema and access once (owner, selected database)

The existing private `~/.config/requi/preview.env` must point at the intended
app/admin database pair. The database can be shared by local and server
instances; if targets use distinct databases, repeat this step for each intended
private settings file through the repo migration entry, not by swapping URLs
blindly. No connection string belongs in chat.

```bash
req migrate
req migrate --apply
```

Run apply only after reviewing the dry inventory. Expected: matching app/admin
target, complete migration inventory, restricted SET memberships and named
pending files. Review the stopped-child closure migration
`20261104210000_stopped_child_job_closure.sql`. Apply keeps its own confirmations,
including any projection-function review. Do not replay old migrations.
Failure rolls back only the failing file; earlier applied files persist. Rerun
dry review to see what remains. There is no automatic down-migration.

If acceptance specifically reports missing marked-test permission, use the
existing reviewed sole-owner enrollment (not a broader grant):

```bash
req backoffice-access --test-owner
req backoffice-access --test-owner --apply
```

Expected: exact signed-in owner and `sessions.operate` on the reviewed registry
row, after the explicit enrollment confirmation. Omit this conditional step
when already ready. Restart the affected runtime after settings changes.

## 3. Start local preview and deploy the same receipt (owner)

```bash
req preview --apply
```

Expected: accepted image, healthy local preview at http://127.0.0.1:8001,
then private Back office at http://127.0.0.1:8020. Leave this terminal open;
Ctrl-C closes only the Back office listener, not the container. No task starts.
If `requi-preview-exec` already exists, inspect it first. Only after confirming
it is the old owned preview, preserve it rather than force-delete it:

```bash
docker inspect --format '{{.Id}} {{.Image}} {{.State.Status}}' requi-preview-exec
docker stop --time 30 requi-preview-exec
docker rename requi-preview-exec requi-preview-exec-previous
req preview --apply
```

Use a unique previous name if that name exists. Local rollback, only after
confirming the candidate name and old container: stop/remove the new
`requi-preview-exec`, rename the preserved previous container back, start it,
then inspect health and signed readiness. Do not remove a container carrying
unreviewed work. Schema is not rolled back by container rollback.

In another terminal, select the CURRENT reviewed deployment host and app origin
from your own deployment record. The workflow refuses unknown SSH host keys;
do not disable verification. This runbook deliberately does not treat an old
host address as current proof.

```bash
read -r -p 'Reviewed SSH user@host: ' DEPLOY_HOST
read -r -p 'Reviewed HTTPS app origin: ' APP_ORIGIN
req image --deploy ghcr.io/shyalon/delivery-api --host "$DEPLOY_HOST"
```

Dry deploy verifies the private receipt and manifest; it does not contact the
server. Review target defaults: name `requi-delivery`, loopback port 8000,
existing `/etc/requi/full.env`, bridge network. If they differ from the current
service, stop and review explicit `--name`, `--port`, `--env-file`, `--network`.
For first adoption only, run the exact inspect command printed by dry review
and capture its real container ID plus config image ID. Then:

```bash
read -r -p 'Reviewed previous container ID: ' PREVIOUS_CONTAINER_ID
read -r -p 'Reviewed previous config image ID: ' PREVIOUS_IMAGE_ID
req image --deploy ghcr.io/shyalon/delivery-api --host "$DEPLOY_HOST" --previous-container-id "$PREVIOUS_CONTAINER_ID" --previous-image-id "$PREVIOUS_IMAGE_ID" --apply
```

Already workflow-labeled deployments omit both previous-ID flags. The hidden
prompt takes a read-only GHCR token. Do not paste it into chat. Expected:
remote healthy, exact locally tested config and immutable manifest match.
Pull/identity/settings checks happen before the old service stops. Candidate
startup failure requests removal of the owned candidate and restart of the
preserved old container. Inspect actual state and old-container health before
retrying; interrupted SSH is not proof of failure or success. The rollback
container is retained. No proxy changes or database rollback are automatic.

## 4. Check actual headroom and target readiness before paid Start

In the private Back office, confirm reviewed model pins/key and provider balance,
the saved $4 task-generation ceiling, sufficient prepaid credits and Acceptance
readiness. Credit balance must cover the next-phase minimum (default 5 credits)
and actual run charges. Positive balance alone is not enough. Use only an
already-authorized credit allocation route if needed; do not buy a pack or
turn real payments on as a workaround.

With the inspecting database settings verified to be the same database used by
the target:

```bash
req preflight --base-url http://127.0.0.1:8001
req acceptance --dry-run --base-url "$APP_ORIGIN"
```

The first command prompts for the exact signed-up owner email, reads database
policy/headroom and then performs signed local target readiness. If the server
uses another database, its own inspecting database preflight is still required;
a local database report cannot certify server headroom. The second command
signs into the selected server via the normal browser and does read-only target
checks. No task/provider call starts. Confirm both print the intended target.
No separate activation script, go-live switch, per-run max-usd flag or manual
prompt that repeats these same readiness gates is required.

Expected database report: own-test rolling $2, session $3, platform rolling
$3 times registered users, separate saved operator ceiling $4, nonzero remaining
confirmed-cost headroom and `database_policy_ready: true`. It names the first
confirmed guard for a NEW session. Existing session totals are listed separately;
existing generation worst-case exposure is not certified by this read. The
snapshot is not a reservation: concurrent spending and outstanding final bills
can change what fits. Unknown outcomes warn and remain audit evidence; never
retry a possibly billed call blindly. Running calls finish when a confirmed
tripwire stops new calls.

## 5. Owner starts the full runs

Use the normal signed-in app at each target. Create a fresh task with the
intended product brief, review its model/credit information, then choose Start.
Answer the interview and make each real founder decision, including architecture
choices, as the app asks. Continue through needs, SRS, architecture, items, tests
and validation. Do not substitute synthetic answers or auto-approve content.
Inspect the terminal artifacts and per-task debug/cost evidence. Record the
settled total of the first complete current run BEFORE deciding how many more
fit in the remaining rolling headroom.

`req acceptance --execute --base-url ...` is an OPTIONAL, owner-started paid
one-question diagnostic, not the full-run command and not required in addition
to the flow above. Do not run it solely to repeat an already satisfied check.
Stopping work keeps accounting evidence; deletion/purge does not reset costs.
On failure retain debug evidence, inspect the exact refusal, and fix the cause
before another paid Start. Real payments remain off throughout.

## Cost evidence and run counts

Older measured needs-to-review runs reported $0.08 (Sept16 garage claims) and
$0.15 (Sept18 family tasks). They used older code/models and are historical
comparators, not current prices or forecasts. Current retained evidence of
$0.0012972 is just three calls in a partial interview; $0.0132 is an acceptance
SCENARIO, not a measured full run. Neither supports a certified Friday run count.

For illustration only, with a fresh $2 own-test allowance and no other binding
guard, $0.08-sized runs fit 25 times and $0.15-sized runs fit 13 times; three
historical-sized runs would consume $0.24-$0.45. These are arithmetic examples,
not authorization or a guarantee of 3 current runs. Use actual remaining
headroom and the first measured complete current run. The separate $4 exposure
guard can bind earlier than confirmed charges. No limit increase is justified
by current evidence, and none is included. Never clear accounting to make room.
