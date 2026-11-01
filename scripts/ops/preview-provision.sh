#!/usr/bin/env bash
# One-time local setup for the fully local preview: save the live settings the preview needs ON THIS MAC ONLY.
# Run from this folder: bash scripts/ops/preview-provision.sh
# What it does: asks for the live settings with hidden input (they are never printed, never sent anywhere), and stores
# them in a file readable only by you: ${XDG_CONFIG_HOME:-~/.config}/requi/preview.env
# What it is: a persistent local copy of real credentials on your Mac, so the preview stops needing the server after this.
# What it is NOT: no server contact, no new copy beyond this Mac, no deploy, nothing else uses this file except ops preview.
set -euo pipefail
. "$(dirname "$0")/lib.sh"; ops_start preview-provision "$0"; ops_settings
STORE=${PREVIEW_SETTINGS_FILE:-${XDG_CONFIG_HOME:-$HOME/.config}/requi/preview.env}
TTY=${PREVIEW_TTY:-/dev/tty}
exec 9< "$TTY" || abort NO-TTY "input is not a terminal" "Nothing was written." "run this in a real terminal"
echo
echo "This saves the live preview settings ON THIS MAC at:"
echo "  $STORE  (mode 600: only your user can read it)"
echo "It is a persistent local copy of real credentials. Nothing is sent to any server."
echo "After this one-time step, ops preview runs fully locally (no server access)."
if [ -s "$STORE" ]; then
  echo
  echo "A saved settings file already exists. Typing yes updates missing fields and preserves existing settings."
fi
printf 'Type "yes" to save the settings locally on this Mac: '
read -r A <&9 || A=no; [ "$A" = yes ] || abort NOT-CONFIRMED "you did not type yes" "Nothing was written." "run again and type yes"
ASK_RESULT=''
ask(){ local kind=$1 note=$2 allow_empty=${3:-} val name=${2%% *}
  val=${!name:-}
  if [ -n "$val" ]; then ASK_RESULT=$val; return; fi
  while :; do
  printf '%s%s: ' "$note" "$([ -n "$allow_empty" ] && echo ' (optional, Enter to skip)')"
  IFS= read -rs val <&9 || abort NO-TTY "input ended before all settings were entered" "Nothing was written." "run this in a real terminal"
  echo
  if [ -z "$val" ]; then
    if [ -n "$allow_empty" ]; then ASK_RESULT=''; return; fi
    echo "  cannot be empty."; continue
  fi
  case "$kind" in
    db)  case "$val" in postgres://*|postgresql://*) ;; *) echo "  must start with postgres:// or postgresql://"; continue;; esac;;
    url) case "$val" in https://*) ;; *) echo "  must start with https://"; continue;; esac;;
  esac
  ASK_RESULT=$val; return; done; }
ask db  "DELIVERY_DATABASE_URL";          V_DB=$ASK_RESULT
ask url "DELIVERY_SUPABASE_URL";          V_URL=$ASK_RESULT
ask any "DELIVERY_SUPABASE_ANON_KEY";     V_KEY=$ASK_RESULT
ask any "DELIVERY_SUPABASE_ADMIN_SUBJECTS (comma-separated Supabase user ids that act as admin)"; V_SUB=$ASK_RESULT
ask any "DELIVERY_ADMIN_TOKEN" optional;  V_TOK=$ASK_RESULT
export DELIVERY_DATABASE_URL="$V_DB" DELIVERY_SUPABASE_URL="$V_URL" DELIVERY_SUPABASE_ANON_KEY="$V_KEY" DELIVERY_SUPABASE_ADMIN_SUBJECTS="$V_SUB" DELIVERY_ADMIN_TOKEN="$V_TOK"
.venv/bin/python - "$STORE" <<'PY'
import importlib.util,os,sys
spec=importlib.util.spec_from_file_location('settings','scripts/ops/settings.py');s=importlib.util.module_from_spec(spec);spec.loader.exec_module(s)
# The owner already typed yes above. Keep unrelated saved keys, atomically.
s.save_confirmed(sys.argv[1],{k:os.environ[k] for k in ('DELIVERY_DATABASE_URL','DELIVERY_SUPABASE_URL','DELIVERY_SUPABASE_ANON_KEY','DELIVERY_SUPABASE_ADMIN_SUBJECTS','DELIVERY_ADMIN_TOKEN') if os.environ.get(k)},confirm=lambda _: 'yes')
PY
exec 9<&-
echo
echo "saved (values are never printed):"
echo "  DELIVERY_DATABASE_URL            saved"
echo "  DELIVERY_SUPABASE_URL            saved"
echo "  DELIVERY_SUPABASE_ANON_KEY       saved"
echo "  DELIVERY_SUPABASE_ADMIN_SUBJECTS saved"
echo "  DELIVERY_ADMIN_TOKEN             $([ -n "$V_TOK" ] && echo saved || echo skipped)"
PERM=$(stat -c %a "$STORE" 2>/dev/null || stat -f %Lp "$STORE")
[ "$PERM" = 600 ] || abort PERMS "the saved file is not mode 600 ($PERM)" "Fix it yourself, it holds real credentials:" "chmod 600 $STORE"
echo "== saved at $STORE (mode 600, this Mac only). Run: bash scripts/ops/preview.sh"
