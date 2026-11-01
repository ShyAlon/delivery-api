#!/usr/bin/env bash
# Puts the repo's req runner where your current `req` is, after you see the difference and type yes once.
set -euo pipefail
OPS_NOTE="Nothing was changed on your machine."
. "$(dirname "$0")/lib.sh"; ops_start req-install "$0"; REPO=$PWD
SRC=$REPO/scripts/ops/req
[ -f "$SRC" ] || abort NO-RUNNER "$SRC is missing on main" "" "git -C $REPO log --oneline -3"
CUR=$(command -v req || true)
if [ -z "$CUR" ]; then
  for d in "$HOME/.local/bin" "$HOME/bin" /usr/local/bin; do
    case ":$PATH:" in *":$d:"*) [ -d "$d" ] && [ -w "$d" ] && { CUR=$d/req; break; };; esac
  done
fi
[ -n "$CUR" ] || abort NO-BIN-DIR "no existing req and no writable folder on your PATH among ~/.local/bin, ~/bin, /usr/local/bin" "" "mkdir -p ~/.local/bin && echo 'export PATH=\"\$HOME/.local/bin:\$PATH\"' >> ~/.zshrc && exec zsh"
if [ -L "$CUR" ] && [ "$(readlink "$CUR")" = "$SRC" ]; then echo "ok  $CUR already is the repo's runner. Nothing to do."; exit 0; fi
echo "Your req:   $CUR"
echo "Repo req:   $SRC"
if [ -f "$CUR" ]; then echo "== what changes (lines starting with - leave, + arrive):"; diff -u "$CUR" "$SRC" | tail -n +3 | head -80 || true; fi
printf 'Type "yes" to replace %s with the repo runner (the old file is kept as %s.bak): ' "$CUR" "$CUR"; read -r A
[ "$A" = yes ] || abort DECLINED "you did not type yes" "" "scripts/ops/req-install.sh   (run it again when ready)"
[ ! -f "$CUR" ] || cp "$CUR" "$CUR.bak"
ln -sfn "$SRC" "$CUR" || abort COPY "cannot link $CUR" "" "ls -l $CUR"
echo "DONE. req pointer installed: $CUR (previous copy: $CUR.bak). Use it as before."
