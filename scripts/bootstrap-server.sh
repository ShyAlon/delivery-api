#!/usr/bin/env bash
# Run once as root on a fresh Ubuntu Hetzner VM. Safe to rerun.
set -eu

if [ "$(id -u)" -ne 0 ]; then
  echo "FAILED"
  echo "Cause: server bootstrap needs root privileges."
  echo "Next: run: sudo $0"
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
if [ -f /root/.ssh/authorized_keys ] && [ ! -s /home/deploy/.ssh/authorized_keys ]; then
  cp /root/.ssh/authorized_keys /home/deploy/.ssh/authorized_keys
fi
if [ -f /home/deploy/.ssh/authorized_keys ]; then
  chown deploy:deploy /home/deploy/.ssh/authorized_keys
  chmod 0600 /home/deploy/.ssh/authorized_keys
fi

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
