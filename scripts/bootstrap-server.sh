#!/usr/bin/env bash
# Dry by default. Explicit execution is for an approved fresh Ubuntu/Debian host.
set -eu

# Dry by default. Bootstrap changes firewall/users/host state and must only be
# run on an explicitly approved target. This repository does not authorize it.
if [ "${1:-}" != --execute ]; then
  echo 'DRY: install packages/Docker, explicit deploy public key, private config dir, firewall and fail2ban. No host changes.'
  echo 'Execution requires --execute and REQUI_DEPLOY_PUBLIC_KEY_FILE (reviewed public key only).'
  exit 0
fi
PUBLIC_KEY_FILE=${REQUI_DEPLOY_PUBLIC_KEY_FILE:?explicit reviewed deploy public key file required}
[ -f "$PUBLIC_KEY_FILE" ] && [ ! -L "$PUBLIC_KEY_FILE" ] || { echo 'Regular public key file required'; exit 1; }
python3 - "$PUBLIC_KEY_FILE" <<'PYKEY'
import sys
lines=[line.strip() for line in open(sys.argv[1]) if line.strip()]
if len(lines)!=1 or not lines[0].startswith(('ssh-ed25519 ','ssh-rsa ','ecdsa-sha2-')):
 raise SystemExit('One explicit SSH public key required; never copy root keys or private keys')
PYKEY

# Validate existing destination before apt/user/firewall changes.
[ ! -L /home/deploy/.ssh ] && [ ! -L /home/deploy/.ssh/authorized_keys ] || { echo 'Deploy key destination must not be a symlink'; exit 1; }
if [ -s /home/deploy/.ssh/authorized_keys ] && ! cmp -s "$PUBLIC_KEY_FILE" /home/deploy/.ssh/authorized_keys; then
  echo 'Existing deploy key differs. Review it before any host changes.'; exit 1
fi

if [ "$(id -u)" -ne 0 ]; then
  echo "FAILED"
  echo "Cause: server bootstrap needs root privileges."
  echo "Next: review target and public key, then run explicitly with --execute as root."
  exit 1
fi
. /etc/os-release
if [ "${ID:-}" != ubuntu ] && [ "${ID:-}" != debian ]; then
  echo "FAILED"
  echo "Cause: this bootstrap supports Ubuntu or Debian; found ${ID:-unknown}."
  echo "Next: create an Ubuntu 24.04 server, then rerun this script."
  exit 1
fi

step(){ printf '\n== %s ==\n' "$1"; }
step "Base packages"
apt-get update -qq
DEBIAN_FRONTEND=noninteractive apt-get install -y ca-certificates curl gnupg ufw fail2ban

step "Docker"
if ! command -v docker >/dev/null 2>&1; then
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/"$ID"/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
  chmod a+r /etc/apt/keyrings/docker.gpg
  arch=$(dpkg --print-architecture)
  codename=${VERSION_CODENAME:-$(. /etc/os-release && echo "$VERSION_CODENAME")}
  echo "deb [arch=$arch signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/$ID $codename stable" > /etc/apt/sources.list.d/docker.list
  apt-get update -qq
  DEBIAN_FRONTEND=noninteractive apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin
else
  echo "already installed"
fi
systemctl enable --now docker

step "Deploy user and persistent data"
if ! id deploy >/dev/null 2>&1; then
  useradd --create-home --shell /bin/bash deploy
  echo "created deploy"
else
  echo "deploy already exists"
fi
usermod -aG docker deploy
install -d -o deploy -g deploy -m 0750 /data
install -d -o deploy -g deploy -m 0700 /home/deploy/.ssh
if [ -s /home/deploy/.ssh/authorized_keys ]; then
  if ! cmp -s "$PUBLIC_KEY_FILE" /home/deploy/.ssh/authorized_keys; then
    echo 'Existing deploy key differs. Review it; refusing to replace or widen access.'; exit 1
  fi
else
  install -o deploy -g deploy -m 0600 "$PUBLIC_KEY_FILE" /home/deploy/.ssh/authorized_keys
fi
install -d -o deploy -g deploy -m 0700 /etc/requi
# Docker group is host-root-equivalent, not an application role. Do not add
# application/operator users or infer an operator identity from deploy access.

step "Firewall"
ufw default deny incoming
ufw default allow outgoing
ufw allow OpenSSH
ufw allow 80/tcp
ufw allow 443/tcp
ufw --force enable
ufw status

step "Login protection"
cat > /etc/fail2ban/jail.d/sshd.local <<'EOF'
[sshd]
enabled = true
bantime = 1h
findtime = 10m
maxretry = 5
EOF
systemctl enable --now fail2ban
systemctl restart fail2ban

echo
echo "Server bootstrap complete."
echo "Docker: $(docker --version)"
echo "Data: /data (owned by deploy)"
echo "Firewall: SSH, HTTP and HTTPS inbound only. App port 8000 remains localhost-only."
echo "Next: from your computer, verify: ssh deploy@$(hostname -I | awk '{print $1}') docker version"
