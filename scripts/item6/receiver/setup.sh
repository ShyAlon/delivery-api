#!/usr/bin/env bash
# Run only on the reviewed fresh VPS. Dry by default. Never takes passwords/keys.
set -Eeuo pipefail
umask 077
fail(){ printf '{"checked":"%s","expected":"%s","found":"%s","where":"receiver VPS","completed":false}\n' "$1" "$2" "$3"; exit 1; }
step(){ printf '{"checked":"%s","expected":"reviewed prerequisite","found":"passed","where":"receiver VPS"}\n' "$1"; }
trap 'fail "setup command" "successful explicit step" "command failed; inspect locally, do not paste credential logs"' ERR
MODE=${1:-dry};IP=${2:-};EMAIL=${3:-}
[[ "$MODE" =~ ^(dry|install|tls|check|harden-ssh)$ ]] || fail mode 'dry/install/tls/check/harden-ssh' 'unsupported mode'
if ! python3 - "$IP" <<'PY'
import ipaddress,sys
try:
 ip=ipaddress.ip_address(sys.argv[1]);assert ip.version==4 and ip.is_global
except Exception:raise SystemExit('Public IPv4 argument required, not localhost/private/multicast')
PY
then fail 'public IPv4' 'globally routed IPv4 of this VPS' 'invalid/private/missing input'; fi
SOURCE=$(cd -- "$(dirname -- "$0")" && pwd)
[[ -f "$SOURCE/receiver.py" ]] || fail package 'receiver.py beside setup.sh' 'missing'
if [[ "$MODE" == dry ]]; then
 printf '{"checked":"receiver setup plan","expected":"fresh approved Ubuntu VPS, public IPv4, key login, trusted public TLS","found":"dry only; no changes","where":"receiver VPS","network_started":false,"payment_effect":false}\n'
 echo 'Next: install <IP>; tls <IP> <ACME email>; check <IP>. SSH hardening is a separate final action after a SECOND key-only login succeeds.'
 exit 0
fi
[[ $(id -u) == 0 ]] || fail privileges 'sudo/root on approved VPS' 'not root'
. /etc/os-release
[[ "$ID" == ubuntu && "$VERSION_ID" =~ ^(24\.04|26\.04)$ ]] || fail OS 'Ubuntu24.04 or26.04; checked rather than assumed' 'unsupported OS'
[[ -n "${SSH_CONNECTION:-}" ]] || fail 'machine boundary' 'reviewed SSH session; preserve SSH_CONNECTION with sudo' 'SSH session marker missing'
IFS=' ' read -r CLIENT CLIENT_PORT SERVER SSH_PORT <<< "$SSH_CONNECTION"
[[ "$SSH_PORT" == 22 ]] || fail 'SSH port' 'standard22 on fresh VPS' 'nonstandard port; preserve current firewall, review setup'
ip -4 addr show | grep -Fq "inet $IP/" || fail 'local IP ownership' 'explicit public IPv4 assigned to this host' 'not assigned locally'
# Never change unrelated existing webservers, keys, firewall rules, or Caddy config.
BASE=/etc/requi-receiver
if [[ "$MODE" == install ]]; then
 [[ ! -e "$BASE" && ! -L "$BASE" ]] || fail 'fresh receiver destination' 'new /etc/requi-receiver; no overwrite' 'already exists; inspect instead of retrying'
 [[ ! -e /etc/caddy/Caddyfile ]] || fail 'fresh TLS configuration' 'no existing Caddy config' 'existing config; preserve it'
 [[ ! -e /opt/requi-receiver ]] || fail 'fresh service files' 'new receiver directory' 'existing destination'
 if ss -ltnH | awk '{print $4}' | grep -Eq ':(80|443|8787)$'; then fail 'port ownership' '80/443/8787 unused on fresh VPS' 'listener already present'; fi
 id requi-receiver >/dev/null 2>&1 && fail 'service account' 'new isolated account' 'account exists'
 step 'fresh destinations and ports'
 apt-get update -qq > /var/log/requi-receiver-install.log 2>&1
 DEBIAN_FRONTEND=noninteractive apt-get install -y python3 python3-venv caddy ca-certificates curl openssl ufw >> /var/log/requi-receiver-install.log 2>&1
 # Distro Certbot may be too old for public IP issuance. Separate venv, current package
 # from official PyPI; capability/version check below. No private package tokens.
 python3 -m venv /opt/requi-certbot
 /opt/requi-certbot/bin/pip install 'certbot>=5.4,<6' >> /var/log/requi-receiver-install.log 2>&1
 /opt/requi-certbot/bin/certbot --help all | grep -q -- '--ip-address' || fail Certbot 'IP issuance support >=5.4' 'flag unavailable'
 useradd --system --home /var/lib/requi-receiver --shell /usr/sbin/nologin requi-receiver
 install -d -m700 "$BASE"
 printf '%s\n' "$IP" > "$BASE/public-ip"
 install -d -o root -g root -m755 /opt/requi-receiver
 install -m644 "$SOURCE/receiver.py" /opt/requi-receiver/receiver.py
 install -d -o requi-receiver -g requi-receiver -m700 /var/lib/requi-receiver /var/lib/requi-receiver/spool
 install -d -o root -g caddy -m750 "$BASE/tls"
 install -d -o caddy -g caddy -m755 /var/lib/requi-acme
 cat > /etc/systemd/system/requi-receiver.service <<'EOF'
[Unit]
Description=Passive sandbox PayPal evidence receiver
After=network.target
[Service]
User=requi-receiver
Group=requi-receiver
ExecStart=/usr/bin/python3 /opt/requi-receiver/receiver.py serve
Restart=on-failure
UMask=0077
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/var/lib/requi-receiver
MemoryMax=128M
TasksMax=32
LimitNOFILE=256
[Install]
WantedBy=multi-user.target
EOF
 cat > /etc/caddy/Caddyfile <<EOF
{
 auto_https off
}
http://$IP {
 handle /.well-known/acme-challenge/* {
  root * /var/lib/requi-acme
  file_server
 }
 handle {
  respond "Not found" 404
 }
}
EOF
 chmod 644 /etc/caddy/Caddyfile /etc/systemd/system/requi-receiver.service
 # Do not reset firewall or remove prior rules; allow standard SSH before enabling.
 ufw allow 22/tcp >> /var/log/requi-receiver-install.log 2>&1
 ufw allow 80/tcp >> /var/log/requi-receiver-install.log 2>&1
 ufw allow 443/tcp >> /var/log/requi-receiver-install.log 2>&1
 ufw --force enable >> /var/log/requi-receiver-install.log 2>&1
 systemctl daemon-reload
 systemctl enable --now requi-receiver caddy >> /var/log/requi-receiver-install.log 2>&1
 caddy reload --force --config /etc/caddy/Caddyfile
 step 'passive receiver and ACME HTTP listener installed'
 echo 'TLS not issued yet. Check provider firewall permits80/443. No webhook registration or payment yet.'
 exit 0
fi
[[ -f "$BASE/public-ip" && ! -L "$BASE/public-ip" && $(cat "$BASE/public-ip") == "$IP" ]] || fail 'host IP binding' 'same explicitly installed public IPv4' 'missing/different binding'
if [[ "$MODE" == tls ]]; then
 [[ "$EMAIL" =~ ^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$ ]] || fail 'ACME email' 'email for certificate account, not a secret' 'invalid/missing'
 [[ ! -e "$BASE/tls/ready" ]] || fail 'TLS setup once' 'no existing ready TLS; use check/renew' 'already configured'
 # Validate challenge externally BEFORE proceeding. Public address must reach this host.
 CHALLENGE=$(python3 -c 'import secrets;print(secrets.token_hex(16))')
 # Set each ancestor explicitly: install -d can leave a preexisting parent700.
 for PUBLIC_DIR in /var/lib/requi-acme /var/lib/requi-acme/.well-known /var/lib/requi-acme/.well-known/acme-challenge; do
  [[ ! -L "$PUBLIC_DIR" ]] || fail 'public ACME path safety' 'no symlinks in challenge tree' 'symlink found; preserve and review'
  install -d -o caddy -g caddy -m755 "$PUBLIC_DIR"
 done
 printf '%s' "$CHALLENGE" > /var/lib/requi-acme/.well-known/acme-challenge/receiver-check
 chmod 644 /var/lib/requi-acme/.well-known/acme-challenge/receiver-check
 sudo -u caddy test -r /var/lib/requi-acme/.well-known/acme-challenge/receiver-check || fail 'ACME file traversal' 'Caddy can read public challenge' 'permission denied; private spool untouched'
 if ! python3 - "$IP" <<'PROBE'
import json,pathlib,subprocess,sys
p=pathlib.Path('/var/lib/requi-acme/.well-known/acme-challenge/receiver-check')
r=subprocess.run(['curl','--noproxy','*','--silent','--show-error','--max-time','15','--write-out','\n%{http_code}','http://'+sys.argv[1]+'/.well-known/acme-challenge/receiver-check'],capture_output=True)
body,_,code=r.stdout.rpartition(b'\n')
ok=r.returncode==0 and code==b'200' and body==p.read_bytes()
category='challenge readable and exact' if ok else 'HTTP403: Caddy refused challenge; inspect traversal permissions/route' if code==b'403' else 'HTTP404: challenge route/file missing' if code==b'404' else 'network request failed; inspect routing/firewalls' if r.returncode else 'unexpected HTTP status or challenge-body mismatch'
print(json.dumps({'checked':'ACME HTTP reachability','expected':'HTTP200 and exact public challenge bytes','found':{'category':category,'http_status':code.decode(errors='replace'),'curl_exit':r.returncode,'matches_file':body==p.read_bytes()},'where':'public ACME path from VPS','completed':ok}))
raise SystemExit(0 if ok else 1)
PROBE
 then exit 1; fi
 # Public CA traffic discloses IP/email only, not payment data. No staging cert served.
 /opt/requi-certbot/bin/certbot certonly --non-interactive --agree-tos --email "$EMAIL" --preferred-profile shortlived --webroot --webroot-path /var/lib/requi-acme --ip-address "$IP" --cert-name requi-receiver-ip > /var/log/requi-receiver-certbot.log 2>&1
 cat > /opt/requi-receiver/reload-cert.sh <<'EOF'
#!/bin/sh
set -eu
umask 077
base=/etc/requi-receiver/tls
install -o root -g caddy -m640 /etc/letsencrypt/live/requi-receiver-ip/fullchain.pem "$base/fullchain.pem.new"
install -o root -g caddy -m640 /etc/letsencrypt/live/requi-receiver-ip/privkey.pem "$base/privkey.pem.new"
mv "$base/fullchain.pem.new" "$base/fullchain.pem"
mv "$base/privkey.pem.new" "$base/privkey.pem"
caddy reload --force --config /etc/caddy/Caddyfile
EOF
 chmod 700 /opt/requi-receiver/reload-cert.sh
 # Parent readable to caddy for cert traversal only; other config remains root600.
 chmod 750 "$BASE";chown root:caddy "$BASE"
 install -o root -g caddy -m640 /etc/letsencrypt/live/requi-receiver-ip/fullchain.pem "$BASE/tls/fullchain.pem"
 install -o root -g caddy -m640 /etc/letsencrypt/live/requi-receiver-ip/privkey.pem "$BASE/tls/privkey.pem"
 cat > /etc/caddy/Caddyfile <<EOF
{
 auto_https off
}
http://$IP {
 handle /.well-known/acme-challenge/* {
  root * /var/lib/requi-acme
  file_server
 }
 handle {
  respond "Not found" 404
 }
}
https://$IP {
 tls /etc/requi-receiver/tls/fullchain.pem /etc/requi-receiver/tls/privkey.pem
 @webhook {
  method POST
  path /sandbox/paypal/webhook
 }
 handle @webhook {
  request_body {
   max_size 256KB
  }
  reverse_proxy 127.0.0.1:8787
 }
 handle {
  respond "Not found" 404
 }
}
EOF
 caddy validate --config /etc/caddy/Caddyfile > /var/log/requi-receiver-caddy-validate.log 2>&1
 caddy reload --force --config /etc/caddy/Caddyfile
 cat > /etc/systemd/system/requi-receiver-renew.service <<'EOF'
[Unit]
Description=Renew short-lived receiver public IP certificate
[Service]
Type=oneshot
ExecStart=/opt/requi-certbot/bin/certbot renew --cert-name requi-receiver-ip --quiet --deploy-hook /opt/requi-receiver/reload-cert.sh
EOF
 cat > /etc/systemd/system/requi-receiver-renew.timer <<'EOF'
[Unit]
Description=Check receiver certificate renewal twice daily
[Timer]
OnCalendar=*-*-* 00,12:00:00
RandomizedDelaySec=30m
Persistent=true
[Install]
WantedBy=timers.target
EOF
 systemctl daemon-reload
 systemctl enable --now requi-receiver-renew.timer
 /opt/requi-certbot/bin/certbot renew --cert-name requi-receiver-ip --dry-run > /var/log/requi-receiver-renew-test.log 2>&1
 printf 'ready\n' > "$BASE/tls/ready"
 step 'public IP certificate, reload hook and renewal dry-run'
fi
if [[ "$MODE" == harden-ssh ]]; then
 [[ ${REQUI_SECOND_KEY_LOGIN_CONFIRMED:-} == YES ]] || fail 'key-login proof' 'second independent key-only SSH login succeeded, old session kept open' 'no explicit local proof'
 [[ ! -e /etc/ssh/sshd_config.d/00-requi-key-only.conf ]] || fail 'SSH config safety' 'no existing receiver hardening file' 'already exists'
 cat > /etc/ssh/sshd_config.d/00-requi-key-only.conf <<'EOF'
PasswordAuthentication no
KbdInteractiveAuthentication no
PubkeyAuthentication yes
PermitRootLogin prohibit-password
EOF
 if ! sshd -t; then
  mv /etc/ssh/sshd_config.d/00-requi-key-only.conf "$BASE/ssh-rejected.conf"
  fail 'SSH config' 'valid new config; original active policy kept' 'new config rejected and held privately; no reload'
 fi
 # Check effective policy for the actual connected user/address, including Match rules.
 IFS=' ' read -r CLIENT _ SERVER _ <<< "$SSH_CONNECTION"
 LOGIN=${SUDO_USER:-root}
 POLICY=$(sshd -T -C "user=$LOGIN,host=$CLIENT,addr=$CLIENT,laddr=$SERVER,lport=22")
 if ! grep -q '^passwordauthentication no$' <<< "$POLICY" || ! grep -q '^kbdinteractiveauthentication no$' <<< "$POLICY"; then
  mv /etc/ssh/sshd_config.d/00-requi-key-only.conf "$BASE/ssh-rejected.conf"
  fail 'effective SSH policy' 'password and keyboard interactive off' 'conflicting SSH settings; keep old session open, inspect locally'
 fi
 systemctl reload ssh
 step 'key-only SSH policy loaded; verify another key login before closing session'
 exit 0
fi
[[ -e "$BASE/tls/ready" ]] || fail 'trusted TLS' 'tls step completed' 'not ready'
systemctl is-active --quiet requi-receiver caddy requi-receiver-renew.timer
curl --noproxy '*' --fail --silent --max-time 10 http://127.0.0.1:8787/health > /dev/null
openssl x509 -in "$BASE/tls/fullchain.pem" -noout -checkend 172800 > /dev/null || fail 'certificate runway' 'more than48h left' 'near expiry; inspect renewal'
# Do NOT use -k: verify public trust+IP. Public ingress rejects malformed/no-header data.
STATUS=$(curl --noproxy '*' --silent --show-error --max-time 15 -o /dev/null -w '%{http_code}' -H 'Content-Type: application/json' -d '{}' "https://$IP/sandbox/paypal/webhook")
[[ "$STATUS" == 400 ]] || fail 'trusted HTTPS ingress' 'valid public chain/IP and400 for unsigned invalid event' 'unexpected response; not ready'
step 'trusted TLS/IP, passive ingress, private spool and renewal timer'
printf 'Register only this sandbox URL: https://%s/sandbox/paypal/webhook\n' "$IP"
echo 'Event: PAYMENT.CAPTURE.COMPLETED. Simulator proves delivery only, not real payment. No production or item6 run authorized.'
