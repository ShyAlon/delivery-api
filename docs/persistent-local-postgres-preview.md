# Persistent local data, real hosted sign-in

Owner opt-in orchestration, not an application mode. The exact accepted app
image and all request, worker, PostgreSQL, RLS and provider code stay unchanged.
The database is an ownership-labeled local named volume. Hosted Supabase Auth
and JWKS still use HTTPS443, so this works around a blocked database port but
is not offline. Deploy over SSH once from a network that permits SSH. No phone
hotspot is required for the local data connection.

## Proposed owner steps, pending Docker-host validation

This feature is not Docker-host validated yet. Do not treat unit tests as proof
of its Docker lifecycle. From the reviewed branch before merge use the checked-in
entry directly; `req` always syncs main and cannot run an unmerged branch.

After merge and the actual Docker-host gate:

```
req image --accept-local
req image --local-pg start --backoffice-port 8021 --apply
```

The first accepts the final image once. The second prepares the separately
built local PostgreSQL dependency, starts that SAME app image at
http://127.0.0.1:8002, opens its private Back office8021, and opens a dedicated
browser. Sign in there normally to the real hosted Auth service. Keep this
terminal and dedicated browser open. Do not use an unrelated browser cookie.
The controller verifies the JWT signature/issuer/audience/subject/expiry and
hosted `/auth/v1/user` before enrolling only that owner/session in local PG.
The reviewed operator subject must already be in the private source settings;
an arbitrary account is never promoted. On browser refresh, normal hosted
refresh runs and its verified session is enrolled before the response is
released to the app. On expiry or failed hosted identity check, enrollment is
revoked, not extended. Sign in again in this same browser. Closing the browser
or controller requests revocation of its local sessions. The PG companion
also removes expired session rows every second, even if the controller exits.

This cannot promise immediate hosted revocation parity: hosted `/user` and a
valid access JWT can remain valid until their expiry after some revocations.
The local database does not mirror hosted `auth.sessions`. App signature and
expiry checks remain unchanged, and local session presence is not advertised
as production Auth parity.

First enrollment creates 100 LOCAL test credits once and the reviewed local
operator capabilities, $2 own-test/$3 session and platform/$4 exposure policies.
Subsequent sign-ins do not refill credits or change policies. No hosted balance,
operator registry, task, payment or accounting is read or copied. Real payments
are off: no payment credential settings cross into the generated local file.
Model pins are NOT invented or read from a live database. Before Start, review
and approve them in the normal private Back office. A missing pin correctly
keeps execution unavailable. Review the separate provider exposure ceiling and
actual credit/guard headroom there. An explicit private DeepSeek key may be
carried as a value from the selected source settings; setup never dispatches a
provider call. Only the owner's deliberate Start may spend money.

## What local validation does and does not mean

The app uses the same migrations, backend SET roles and session claims. A
local run can catch actual PostgreSQL/RLS and child-handoff defects. It does
not prove hosted pooler/session limits, hosted grants/config, production data,
remote latency, vendor model quality or current deployed readiness. This
companion's auth-schema contract is not the complete hosted Supabase service.

The current acceptance fixture gives a zero-charge short Start/answer/Stop.
It is not an intelligent full-flow model. Loading phi4 still needs actual Mac
Ollama memory and a Docker-reachable endpoint, never container-localhost for a
native host service. A successful model-list probe does not certify RAM fit.
DeepSeek over HTTPS443 is the practical real-model alternative only when the
owner chooses it; there is no silent paid fallback or model substitution.

Local accounting is independent of hosted accounting. Local guards CANNOT see
hosted/provider charges recorded elsewhere. Reset is not replenishment of a
vendor allowance. Preserve costs before further paid work. Destructive reset
refuses if ANY paid attempt evidence remains, including uncertain outcomes.
It never deletes that evidence just to make another run fit.

## Stop, restart and explicit reset

```
req image --local-pg stop --apply
req image --local-pg start --backoffice-port 8021 --apply
```

Stop revokes local sessions, removes only the ownership-checked app container,
and stops its companion. The named data volume remains. Restart keeps data,
credits, costs and approved pins. There is no import or sync to Supabase.
After later source changes accept the new image and review pending local
migration checksums; a changed already-applied SQL file refuses rather than
replaying it.

```
req image --local-pg reset --apply
```

Reset has its own explicit deletion confirmation and refuses paid evidence.
Only its exact ownership-labeled local volumes can be removed. Supabase DSNs
are never connected, rewritten or used as a fallback. Source private settings
are read through a small allowlist; data/admin DSNs and extra provider/payment
settings are not copied. Generated settings must use the fixed owned Unix
socket DSN. The PG companion has `--network none`, no TCP listener/publication,
peer-mapped restricted application login, and no provider/admin keys. The app
receives the socket volume read-only, not the PostgreSQL data volume or admin
identity. An ownership/name/mount/network mismatch refuses before SQL or cleanup.

On failure keep output and data, do not repeat paid Start or delete evidence.
Use the task's Account > Debug > Download debug ZIP when the page is available.
Existing `req debug` targets the older standard preview container, not this
uniquely named local PG app. Local-dependency diagnostic export remains to be
completed before this feature is delivered.
