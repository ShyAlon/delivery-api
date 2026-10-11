# Passive sandbox receiver

This is evidence collection, not a payment service. It never creates a purchase,
verifies/credits a payment, fetches a certificate URL, runs an engine, or enables
production. PayPal input is untrusted until the attended harness's existing
sandbox signature verification and capture/order binding succeed. The receiver
accepts only PAYMENT.CAPTURE.COMPLETED-shaped notifications with five original
signature headers. Forged shape-valid requests can be stored; they never count
as verified evidence. The 256-file /128MiB spool cap stops new arrivals with503
when full, rather than deleting old evidence. Inspect/reconcile capacity locally.

## Fresh-host steps

Ubuntu24.04 or26.04, public IPv4 actually assigned to the host, standard SSH22,
verified SSH host fingerprint and key login. No domain or third-party relay.
Only the reviewed owner runs setup. Dry mode changes nothing.

1. `bash setup.sh dry <IPv4>`
2. On the VPS in an SSH session, with sudo preserving SSH_CONNECTION:
   `sudo --preserve-env=SSH_CONNECTION bash setup.sh install <IPv4>`
3. Ensure provider firewall allows80/443, then:
   `sudo --preserve-env=SSH_CONNECTION bash setup.sh tls <IPv4> <ACME-email>`
4. `sudo --preserve-env=SSH_CONNECTION bash setup.sh check <IPv4>`
5. Independently open a SECOND SSH session with password and interactive auth
   disabled, keeping the original open. Only when this succeeds:
   `sudo --preserve-env=SSH_CONNECTION,REQUI_SECOND_KEY_LOGIN_CONFIRMED env REQUI_SECOND_KEY_LOGIN_CONFIRMED=YES bash setup.sh harden-ssh <IPv4>`
   Verify a third key-only login before closing old sessions.

Installer rejects an existing Caddyfile, receiver destination/account, or listener
on80/443/8787. No overwrite of a prior install. Partial install is not completion:
keep logs privately and reconcile the named failed step before retrying. It
installs public OS packages and Certbot >=5.4,<6 from PyPI in a separate venv.
No package tokens or private keys are transmitted. Package versions are checked
on the actual host. Caddy serves ACME webroot on80, HTTPS ingress on443 and no
public read/list/export API. No access/body/header logging is configured.

Certbot publicly trusted IP certificates expire in160h. Renewal checks twice
per day through a persistent systemd timer, with private cert copy and Caddy
reload hook. The TLS step runs a renewal dry-run. `check` requires a live timer,
valid public TLS/IP without insecure bypass and >48h remaining on certificate.
No ongoing alert service is installed: run `check` again before the attended run.
TLS failure is a blocker, never permission to accept a self-signed certificate.
Sources: https://letsencrypt.org/2026/03/11/shorter-certs-certbot and
https://caddyserver.com/docs/caddyfile/directives/tls .

## Dashboard and evidence

After trusted HTTPS check, register its printed exact URL under the SAME sandbox
REST app used by the run, event PAYMENT.CAPTURE.COMPLETED. Copy subscription ID
(not WH event ID) to item6 private/.env SANDBOX_WEBHOOK_ID as a JSON string.
Review/confirm/compile again. A simulator notification can prove connectivity,
not the authenticity of a real captured payment. No purchase is started by setup.
No --go command is provided. Real capture/run needs separate approval.

Private spool status, on VPS:
`sudo -u requi-receiver python3 /opt/requi-receiver/receiver.py status`
Status prints only count/bytes and local opaque receipt IDs, never payloads.
Export an exact receipt via authenticated SSH to a NEW local chmod600 file:
`sudo -u requi-receiver python3 /opt/requi-receiver/receiver.py export --receipt <receipt>`
Do not run export to the terminal, paste payloads into chat, or use public links.
Inspect the file locally to choose the event matching the run's capture. The
harness verifies and binds it; a random receipt ID/file is not proof of identity.

The raw envelope preserves exact body bytes in base64 plus all five original
PayPal signature headers and a body hash. Format item6-paypal-raw-v1. The updated
attended.py decodes/validates it without reserialization. The previous event+headers
format remains compatible but doesn't preserve raw-body bytes. The Mac image
must contain this adapter change before raw envelopes can be fed to it.

All evidence remains private dirs700/files600 on the VPS and Mac. No automatic
retention deletion, no production activation. Production later needs its own
reviewed app/environment/verification/queue/ledger path and operational proof,
not a live_mode flip on this passive sandbox service.

## Mac runner

`mac-setup.sh dry <IPv4> <SSH-user>` performs no mutation. Install/tls/check upload
setup+receiver into a NEW private remote directory and run the chosen step through
key-only SSH with strict known-host verification. It refuses missing passwordless
sudo; do not weaken key checks or put a sudo password in a pipe. For OVH's initial
password login or sudo prompt use the provider's local interactive instructions,
then prove key-only login. Machine facts persist separately in
`~/.config/requi-receiver/connection.json` chmod600; previous different facts are
not overwritten. No private key copied or stored by this script.

`mac-setup.sh export <IPv4> <SSH-user> <receipt> <NEW-local-file>` directs the
payload into an exclusive new private output, never the terminal. A failed export
may leave a partial private file: preserve/inspect it and choose a new destination
rather than overwriting. SSH status and export are independent of setup uploads.

Package transport may use a .txt self-extracting script. Rename to .sh, run dry
first; it creates private local package scratch outside the repo. Do not install
an unmerged/unreviewed checkpoint on the VPS. Host and TLS commands have not been
executed by the implementer; local tests are not Ubuntu/Caddy/Certbot/PayPal proof.
