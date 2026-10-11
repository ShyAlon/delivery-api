#!/usr/bin/env bash
# Runs on Mac. Explicit install/tls/check/harden modes execute only on reviewed VPS.
set -Eeuo pipefail
umask 077
fail(){ printf '{"checked":"%s","expected":"%s","found":"%s","where":"Mac receiver setup","completed":false}\n' "$1" "$2" "$3"; exit 1; }
MODE=${1:-dry};IP=${2:-};LOGIN=${3:-};EMAIL=${4:-}
[[ "$MODE" =~ ^(dry|install|tls|check|harden-ssh|status|export)$ ]] || fail mode 'documented mode' 'unsupported'
if ! python3 - "$IP" "$LOGIN" <<'PY'
import ipaddress,re,sys
try:
 p=ipaddress.ip_address(sys.argv[1]);assert p.version==4 and p.is_global
 assert re.fullmatch(r'[a-z_][a-z0-9_-]{0,31}',sys.argv[2])
except Exception:raise SystemExit('Public IPv4 and ordinary SSH username required')
PY
then fail 'connection arguments' 'public IPv4 and ordinary SSH user' 'invalid/missing input'; fi
HERE=$(cd -- "$(dirname -- "$0")" && pwd)
META="$HOME/.config/requi-receiver"
if [[ "$MODE" == dry ]]; then
 echo '{"checked":"receiver connection plan","expected":"explicit host/user, known SSH fingerprint and working Mac key","found":"dry; no host or local changes","where":"Mac","completed":true}'
 echo 'install/tls/check change the explicitly selected fresh VPS. No SSH private key leaves this Mac. Never add connection fields to item6 .env.'
 exit 0
fi
# Never auto-accept unknown/changed host keys. Owner first verifies OVH console
# fingerprint and adds host key using ordinary SSH. No ssh-keyscan trust shortcut.
SSH=(ssh -o StrictHostKeyChecking=yes -o PasswordAuthentication=no -o KbdInteractiveAuthentication=no -o BatchMode=yes "$LOGIN@$IP")
"${SSH[@]}" true || fail 'SSH key login/host identity' 'verified known host and key-only login' 'failed; verify fingerprint and provisioning locally'
[[ ! -L "$META" && ! -L "$META/connection.json" ]] || fail 'connection metadata' 'private nonsymlink local directory' 'unsafe destination'
mkdir -p "$META"
chmod 700 "$META"
python3 - "$META/connection.json" "$IP" "$LOGIN" <<'PY'
import json,os,sys
p,ip,user=sys.argv[1:];value={'ipv4':ip,'ssh_user':user}
if os.path.exists(p):
 s=os.stat(p)
 if s.st_mode&0o077 or json.load(open(p))!=value:raise SystemExit('Private stored connection differs; review locally, never overwrite it')
else:
 fd=os.open(p,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
 with os.fdopen(fd,'w') as f:json.dump(value,f)
PY
if [[ "$MODE" == status ]]; then
 "${SSH[@]}" 'sudo -n -u requi-receiver python3 /opt/requi-receiver/receiver.py status'
 exit 0
fi
if [[ "$MODE" == export ]]; then
 RECEIPT=${4:-};OUTPUT=${5:-}
 [[ "$RECEIPT" =~ ^[a-f0-9]{32}$ && -n "$OUTPUT" && ! -e "$OUTPUT" && ! -L "$OUTPUT" ]] || fail 'export destination' 'exact receipt and new owner-only local output path' 'missing/unsafe/existing'
 set -o noclobber
 "${SSH[@]}" "sudo -n -u requi-receiver python3 /opt/requi-receiver/receiver.py export --receipt $RECEIPT" > "$OUTPUT" || fail 'private export' 'complete receipt; preserve any partial output' 'failed; no deletion or retry overwrite'
 chmod 600 "$OUTPUT"
 echo '{"checked":"private receipt export","expected":"authenticated SSH to new local file","found":"exported; still unverified, inspect locally","where":"chosen local output file","payment_effect":false}'
 exit 0
fi
# Avoid passing sudo password over pipe. If SSH user lacks passwordless sudo,
# use OVH-guided local sudo separately; don't put password in chat/script.
"${SSH[@]}" 'sudo -n true' || fail 'sudo capability' 'key login plus passwordless sudo/root on reviewed fresh VPS' 'unavailable; use local interactive sudo, never supply password to script'
# New unique upload directory; preserve attempts, never replace an earlier one.
REMOTE=$("${SSH[@]}" 'umask 077; mktemp -d "$HOME/requi-receiver.XXXXXXXX"')
[[ "$REMOTE" =~ ^/[A-Za-z0-9/._-]+$ ]] || fail 'remote upload path' 'safe new directory returned by reviewed host' 'unexpected'
scp -q -o StrictHostKeyChecking=yes -o PasswordAuthentication=no -o KbdInteractiveAuthentication=no -o BatchMode=yes "$HERE/setup.sh" "$HERE/receiver.py" "$LOGIN@$IP:$REMOTE/"
if [[ "$MODE" == tls ]]; then
 [[ "$EMAIL" =~ ^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$ ]] || fail 'ACME email' 'valid contact email' 'missing/invalid'
 "${SSH[@]}" "sudo -n --preserve-env=SSH_CONNECTION bash $REMOTE/setup.sh tls $IP $EMAIL"
elif [[ "$MODE" == harden-ssh ]]; then
 [[ ${REQUI_SECOND_KEY_LOGIN_CONFIRMED:-} == YES ]] || fail 'second key-only login' 'explicit proof with old session open' 'not confirmed'
 "${SSH[@]}" "sudo -n --preserve-env=SSH_CONNECTION env REQUI_SECOND_KEY_LOGIN_CONFIRMED=YES bash $REMOTE/setup.sh harden-ssh $IP"
else
 "${SSH[@]}" "sudo -n --preserve-env=SSH_CONNECTION bash $REMOTE/setup.sh $MODE $IP"
fi
