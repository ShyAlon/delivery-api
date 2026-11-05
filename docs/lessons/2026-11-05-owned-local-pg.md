# Owned local PostgreSQL without an application fork

Problem: campus WiFi blocks hosted PostgreSQL and ongoing phone tethering costs
battery. Owner November5 chose persistent local PG with real hosted Auth HTTPS,
unchanged accepted image/code path and value-only settings. Hosted subject JWTs
need matching local auth.users/session rows; changing a DSN alone is not enough.

The owner launcher creates a network-disabled PG companion with persistent
ownership-labeled data and socket volumes. Only the restricted app peer login
is accessible from the shared socket. All migrations and fixed role grants run
against the fixed owned socket, never a passed-in URL. Checksums refuse changed
applied files; task data persists. Hosted identity is strictly verified, then
locally enrolled before releasing normal sign-in/refresh responses to the browser.
Expiry is actively deleted because the existing RLS predicate tests row presence,
not not_after. Hosted revocation parity is not claimed. No product session-check
bypass or local behavior flag is added.

A fresh database exposed a generic bootstrap defect: first model-pin approval
called installation only when installation was already ready. The repair event
now tries normal runtime installation; missing keys/pins still refuse normally.
Local tests prove this even when cloud_execution_admitted is false.

Model pins require normal explicit review. No paid rates are invented. Initial
local credits insert once, never refill at sign-in. Local accounting cannot see
hosted charges. Reset stops the app writer before review and refuses any paid
attempt or active job evidence, rather than erase real bills for headroom.

Tests exercise actual fresh PostgreSQL migrations and role SQL, checksum drift,
identity/visibility, persisted credits, expiry and browser cookie interception
ordering. Docker-host peer-UID/volume/container lifecycle remains a separate
required proof; mocks and a non-Docker browser test cannot certify it. No server,
live DB, hosted account or paid provider was used for development validation.
