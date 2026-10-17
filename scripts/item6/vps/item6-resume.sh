#!/usr/bin/env bash
# Resume a stopped item 6 run on the Linux server: restore its own database dump into a throwaway database,
# write off two unknown items (only after you type yes), one new sandbox top-up, then the generator continues
# from its saved checkpoint. Usage:   bash scripts/item6/vps/item6-resume.sh [--rebuild]
# The stopped run is the highest-numbered runs/item6-run-NN on this Mac (override: RESUME_RUN=/path).
set -euo pipefail
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
ITEM6_RUN_LIB=1 source "$HERE/item6-run.sh"
fix(){ echo "Fix: $1"; echo "Then run: bash $HERE/item6-resume.sh"; }
die(){ echo; echo "ABORTED [$1]: $2"; echo "${3:-Nothing was changed.}"; fix "${4:-run it again; if it repeats, send me this whole output}"; exit 1; }
CEILING=2.00
REBUILD=0; for a in "$@"; do case "$a" in --rebuild) REBUILD=1;; *) die USAGE "unknown option $a" "Nothing changed." "bash $HERE/item6-resume.sh [--rebuild]";; esac; done
RUN=${RESUME_RUN:-$(ls -d "$DELIVERY"/runs/item6-run-[0-9][0-9] 2>/dev/null | sort | tail -1)}
[ -n "$RUN" ] && [ -d "$RUN" ] || die NO-RUN "no runs/item6-run-NN directory under $DELIVERY" "Nothing changed." "ls $DELIVERY/runs"
RUN=$(cd "$RUN" && pwd); NAME=$(basename "$RUN")
RID=$(python3 -c "import json;print(json.load(open('$RUN/run-id.json'))['run_id'])" 2>/dev/null) || die RUN-ID "cannot read $RUN/run-id.json" "Nothing changed." "ls $RUN"
[ -f "$RUN/private/.env" ] || die NO-ENV "$RUN/private/.env is missing (needed to read the DeepSeek balance)" "Nothing changed." "ls -la $RUN/private"
RDIR='~/item6/runs/'$NAME
# Resume number: 1 for the first, otherwise one more than the archived resume dumps already on the server (each resume restores the previous one's dump).
N=$("${UBU[@]}" "ls ~/item6/runs/$NAME/evidence 2>/dev/null | grep -c '^item6-database-resume-[0-9]*\\.dump\$' || true" | tail -1 | tr -dc 0-9); [ -n "$N" ] || die VPS-SSH "cannot read the server evidence listing" "Nothing changed." "ssh ubuntu@$HOST ls ~/item6/runs/$NAME/evidence"
N=$((N+1))
SESS=item6r-${RID:0:8}; CONT=requi-item6-resume$N-$RID
TMPF=$(mktemp)
echo "Resume $N of stopped run $NAME (id $RID) on ubuntu@$HOST"
echo "== image"
# The image is current only when every item6 script in it is byte-identical to this checkout's scripts/item6 (not a marker string: a marker goes stale with every later fix).
imgcheck(){ L=$(cd "$DELIVERY/scripts/item6" && shasum -a 256 *.py | sort); R=$("${UBU[@]}" "sudo -n docker run --rm --network none --entrypoint sh requi-item6-test -c 'cd /app/item6 && sha256sum *.py | sort'" 2>/dev/null || true); if [ -n "$R" ] && [ "$L" = "$R" ]; then HAVE=yes; else HAVE=no; printf '%s\n' "$L" > /tmp/img-local.$$; printf '%s\n' "$R" > /tmp/img-image.$$; echo "image differs from $DELIVERY (HEAD $(git -C "$DELIVERY" rev-parse --short HEAD)) in:"; diff /tmp/img-local.$$ /tmp/img-image.$$ | grep '^[<>]' | awk '{print "  "$3}' | sort -u | head -12 || true; rm -f /tmp/img-local.$$ /tmp/img-image.$$; fi; }
imgcheck
if [ "$REBUILD" = 1 ] || [ "$HAVE" != yes ]; then
  [ "$REBUILD" = 1 ] || echo "The image on the server is not built from this checkout's code: it must be rebuilt from your four repos on main (git pull each first)."
  read -r -p "Type yes (English keyboard) to build the image from your repos (several minutes, nothing running is touched), anything else aborts: " A
  [ "$A" = yes ] || die DECLINED "You did not type yes." "Nothing was changed."
  ( stage_build )
  imgcheck
  [ "$HAVE" = yes ] || die IMAGE-NO-RESUME "the rebuilt image still differs from this checkout's scripts/item6 (see above): is delivery-api pulled and clean?" "The image was rebuilt but cannot resume." "git -C $DELIVERY pull --ff-only && bash $HERE/item6-resume.sh --rebuild"
else echo "ok  image matches this checkout's scripts/item6 byte for byte (no rebuild needed)"; fi
echo "== server checks (read-only)"
if ! "${UBU[@]}" "NAME=$NAME RID=$RID SESS=$SESS CONT=$CONT N=$N bash -s" >"$TMPF" <<'PRE'
set -u
cd ~/item6/runs/$NAME 2>/dev/null || { echo "rundir=missing"; exit 0; }
echo "rundir=ok"
echo "blocked=$([ -f evidence/run/blocked.json ] && echo yes || echo no)"
echo "dump=$([ -s evidence/item6-database.dump ] && echo yes || echo no)"
SRC=evidence/item6-database.dump; [ $N -gt 1 ] && SRC=evidence/item6-database-resume-$((N-1)).dump
echo "srcdump=$([ -s $SRC ] && echo yes || echo no)"
echo "dumpsha=$(sha256sum $SRC 2>/dev/null | cut -c1-16)"
echo "fresh_out=$([ ! -e evidence/resume-$N ] && [ ! -e evidence/item6-database-resume-$N.dump ] && echo yes || echo no)"
echo "fresh_backup=$([ ! -e evidence-pre-resume-$N ] && echo yes || echo no)"
# The run-launch verifier refuses to run while evidence/ exists ("preserve prior run, do not retry"). So check the same pinned
# files on a copy of the run directory WITHOUT evidence or this resume's own files. Same hashes, no evidence guard.
VT=$(mktemp -d ~/item6/vtmp.XXXXXX); chmod 700 $VT
find . -maxdepth 1 -mindepth 1 ! -name 'evidence*' ! -name 'topup-webhook' ! -name 'manifest-*' ! -name 'resume-*' ! -name '*.log' ! -name 'go-*' ! -name 'watcher*' ! -name 'webhook-watch*' ! -name 'run-resume.sh' -exec cp -a {} $VT/ \;
mkdir -m 700 $VT/evidence   # the verifier requires an existing, empty, 0700, non-symlink evidence dir
vout=$(cd $VT && python3 private/verify-*.py . 2>&1); vrc=$?; rm -rf $VT
echo "verifier=$([ $vrc = 0 ] && echo ok || echo FAIL)"
echo "verifier_msg=$(printf '%s\n' "$vout" | grep -v '^[[:space:]]*$' | tail -2 | tr '\n' ' ' | cut -c1-300)"
echo "container=$(sudo -n docker ps -a --format '{{.Names}}' | grep -c "^$CONT\$" || true)"
echo "session=$(tmux ls 2>/dev/null | grep -c "^$SESS" || true)"
echo "tmux=$(command -v tmux >/dev/null && echo yes || echo no)"
echo "dockerinfo=$(sudo -n docker info >/dev/null 2>&1 && echo yes || echo no)"
echo "uid=$(id -u)"
echo "mem_mb=$(awk '/MemAvailable/{printf "%d",$2/1024}' /proc/meminfo)"
echo "free_gb=$(df -BG --output=avail / | tail -1 | tr -dc 0-9)"
echo "evidence_mb=$(du -sm evidence | cut -f1)"
echo "listen=$(ss -ltn 2>/dev/null | grep -c '127.0.0.1:8787' || true)"
end=$(echo | timeout 10 openssl s_client -connect 15.235.101.85:443 2>/dev/null | openssl x509 -noout -enddate 2>/dev/null | cut -d= -f2)
echo "cert_hours=$( [ -n "$end" ] && echo $(( ($(date -d "$end" +%s) - $(date +%s)) / 3600 )) || echo -1)"
echo "cert_ok=$(echo | timeout 10 openssl s_client -connect 15.235.101.85:443 -verify_return_error 2>&1 | grep -c 'Verification: OK' || true)"
echo "now=$(date -u +%s)"
for u in api.deepseek.com api-m.sandbox.paypal.com; do echo "net_$u=$(curl -s -o /dev/null -m 10 -w '%{http_code}' https://$u/ || echo 000)"; done
PRE
then die VPS-SSH "cannot ssh to ubuntu@$HOST" "Nothing changed." "ssh ubuntu@$HOST true"; fi
PRE=$(cat "$TMPF"); g(){ printf '%s\n' "$PRE" | sed -n "s/^$1=//p" | head -1; }
[ "$(g rundir)" = ok ] || die NO-RUNDIR "~/item6/runs/$NAME is not on the server" "Nothing changed." "ssh ubuntu@$HOST ls ~/item6/runs"
[ "$(g blocked)" = yes ] && [ "$(g dump)" = yes ] && [ "$(g srcdump)" = yes ] || die NOT-STOPPED "the run has no blocked.json or no database dump on the server: it is not a stopped run this script can resume" "Nothing changed." "bash $HERE/item6-run.sh --resume $RUN status"
[ "$(g fresh_out)" = yes ] && [ "$(g fresh_backup)" = yes ] || die ALREADY-RESUMED "resume $N already has output or a backup on the server" "Nothing was changed. Never overwritten." "ask me for resume number 2"
[ "$(g verifier)" = ok ] || die VERIFIER "the compiled-config verifier (run on a copy without evidence) says: $(g verifier_msg)" "Do not run. Nothing was changed."
[ "$(g container)" = 0 ] || die CONTAINER-EXISTS "container $CONT already exists: preserve it" "Nothing changed."
[ "$(g session)" = 0 ] || die SESSION-EXISTS "tmux session $SESS already exists" "Nothing changed." "bash $HERE/item6-resume.sh is not needed: attach with  ssh -t ubuntu@$HOST tmux attach -t $SESS"
[ "$(g tmux)" = yes ] && [ "$(g dockerinfo)" = yes ] && [ "$(g uid)" = 1000 ] || die VPS-ENV "tmux, docker or the uid-1000 user is missing on the server"
[ "$(g mem_mb)" -ge 2500 ] 2>/dev/null || die VPS-MEM "only $(g mem_mb) MB available"
NEED=$(( $(g evidence_mb) / 1024 * 2 + 4 )); [ "$(g free_gb)" -ge "$NEED" ] 2>/dev/null || die VPS-DISK "only $(g free_gb) GB free; the evidence backup copy plus the run need about $NEED GB"
[ "$(g listen)" -ge 1 ] 2>/dev/null || die RECEIVER "webhook receiver is not listening on 127.0.0.1:8787"
[ "$(g cert_hours)" -ge 48 ] 2>/dev/null && [ "$(g cert_ok)" -ge 1 ] 2>/dev/null || die IP-CERT "IP certificate has $(g cert_hours)h left or does not verify publicly; PayPal would not deliver the webhook"
sk=$(( $(g now) - $(date -u +%s) )); [ ${sk#-} -le 60 ] || die CLOCK-SKEW "VPS clock differs by ${sk}s"
for u in api.deepseek.com api-m.sandbox.paypal.com; do case "$(g net_$u)" in 000|"") die "NET-$u" "$u unreachable from the VPS";; esac; done
echo "ok  stopped run found, dump $(g dumpsha)..., fresh output names, verifier, no old container/session, docker, $(g mem_mb) MB, $(g free_gb) GB free, receiver, IP cert $(g cert_hours)h, clock, network"
echo "== free dry check on the server (restores the dump into a throwaway database; no model call, no network, no payment, nothing written to evidence)"
"${UBU[@]}" "NAME=$NAME RID=$RID N=$N bash -s" <<'GEN' || die GEN-LAUNCHER "could not write the resume launcher on the server" "Nothing else was changed." "ssh ubuntu@$HOST ls ~/item6/runs/$NAME"
set -eu
cd ~/item6/runs/$NAME; umask 077
D=$(ls -d private/runtime-* | head -1); [ "$(ls -d private/runtime-* | wc -l)" = 1 ]
if [ $N = 1 ]; then mkdir -p topup-webhook; chmod 700 topup-webhook; [ -z "$(ls -A topup-webhook)" ] || { echo "topup-webhook is not empty"; exit 1; }; fi
if [ $N = 1 ]; then TOPMOUNT='--mount "type=bind,src=$HERE/topup-webhook,dst=/topup,readonly" -e ITEM6_WEBHOOK_DIR=/topup'; else TOPMOUNT=''; fi
cat > run-resume.sh <<EOS
#!/usr/bin/env bash
# Resume $N of run $RID. Dry unless called as: run-resume.sh --go
cd "\$(dirname "\$0")"
HERE=\$PWD
if [ "\${1:-}" = --go ]; then
  exec sudo -n docker run --name requi-item6-resume$N-$RID -it --memory=2800m --memory-swap=2800m --pids-limit=512 --read-only --cap-drop ALL --security-opt no-new-privileges \\
    --tmpfs /tmp:rw,nosuid,nodev,size=2g --mount "type=bind,src=\$HERE/$D,dst=/private,readonly" --mount "type=bind,src=\$HERE/evidence,dst=/evidence" \\
    $TOPMOUNT --entrypoint python requi-item6-test \\
    /app/item6/resume_entry.py --config /private/run.json --run-id $RID --resume-number $N --go $RID --ceiling-units 2000000000000
fi
exec sudo -n docker run --rm --memory=2800m --memory-swap=2800m --pids-limit=512 --network none --read-only --cap-drop ALL --security-opt no-new-privileges \\
    --tmpfs /tmp:rw,nosuid,nodev,size=2g --mount "type=bind,src=\$HERE/$D,dst=/private,readonly" --mount "type=bind,src=\$HERE/evidence,dst=/evidence,readonly" \\
    $TOPMOUNT --entrypoint python requi-item6-test \\
    /app/item6/resume_entry.py --config /private/run.json --run-id $RID --resume-number $N
EOS
bash -n run-resume.sh; chmod 700 run-resume.sh; echo "ok  run-resume.sh written (dry mounts evidence read-only, no network)"
GEN
DRYOUT=$(mktemp)
if ! "${UBU[@]}" "cd $RDIR && bash run-resume.sh" 2>&1 | tee "$DRYOUT"; then die DRY-FAILED "the free dry check failed (output above)" "Nothing was written and nothing was paid." "send me the output above"; fi
grep -q 'dry check passed' "$DRYOUT" || die DRY-FAILED "the dry check did not report 'dry check passed' (output above)" "Nothing was written and nothing was paid." "send me the output above"
SPENT=$(sed -n 's/.*confirmed provider spend \$\([0-9.]*\).*/\1/p' "$DRYOUT" | head -1); RESV=$(sed -n 's/.*reservation of \$\([0-9.]*\) stays counted.*/\1/p' "$DRYOUT" | head -1)
[ -n "$SPENT" ] && [ -n "$RESV" ] || die DRY-PARSE "could not read the spend figures from the dry check" "Nothing was written and nothing was paid." "send me the output above"
REMAIN=$(python3 -c "print('%.2f'%max(0.0,$CEILING-$SPENT-$RESV-0.005))")
echo "== free source check on the server (WITH network, reads only: DNS, the vendor pages, the DeepSeek balance; no model call, nothing billed or written)"
SRCOUT=$(mktemp)
"${UBU[@]}" "cd $RDIR && D=\$(ls -d private/runtime-* | head -1) && sudo -n docker run --rm --memory=1g --memory-swap=1g --pids-limit=256 --read-only --cap-drop ALL --security-opt no-new-privileges --tmpfs /tmp:rw,nosuid,nodev,size=256m --mount \"type=bind,src=\$PWD/\$D,dst=/private,readonly\" --entrypoint python requi-item6-test /app/item6/source_check.py" 2>&1 | tee "$SRCOUT" || true
grep -q '^source check passed' "$SRCOUT" || die SOURCE-CHECK "the free source check failed (real messages above): the paid run would be refused" "Nothing was written and nothing was paid." "send me the output above"
echo "== DeepSeek balance (this Mac, key not printed)"
BAL=$(RUN="$RUN" python3 - <<'PY'
import json,os,subprocess,sys
key=None
for line in open(os.path.join(os.environ['RUN'],'private/.env')):
    k,s,v=line.partition('=')
    if s and k=='DEEPSEEK_API_KEY':
        try: key=json.loads(v)
        except ValueError: pass
if not isinstance(key,str) or not key: print('NOKEY'); sys.exit(0)
p=subprocess.run(['curl','-sS','-m','20','-K','-','-o','-','-w','\n%{http_code}','https://api.deepseek.com/user/balance'],input='header = "Authorization: Bearer %s"\n'%key,capture_output=True,text=True)
body,_,code=p.stdout.rpartition('\n')
try:
    d=json.loads(body); usd=[b for b in d['balance_infos'] if b['currency']=='USD'][0]
    print('OK',usd['total_balance'])
except Exception: print('FAIL',code)
PY
)
case "$BAL" in OK*) B=${BAL#OK }; echo "balance USD $B";; *) die BALANCE "could not read the DeepSeek balance ($BAL)";; esac
python3 -c "import sys;sys.exit(0 if float('$B')>=$REMAIN else 1)" || die BALANCE-LOW "balance USD $B is below the remaining ceiling USD $REMAIN" "Nothing was started." "top up the DeepSeek account"
echo "Current UTC time: $(date -u '+%Y-%m-%d %H:%M')   (DeepSeek has peak/off-peak pricing: the run should stay inside ONE band)"
echo
echo "============================================================"
echo "THE PAID RESUME. Run id: $RID  (resume $N)"
echo "  server: ubuntu@$HOST  run dir: ~/item6/runs/$NAME  tmux session: $SESS"
echo "  Before anything is paid, in this order, each waiting for you:"
echo "    1. a copy of the evidence is made on the server (evidence-pre-resume-$N); the original stays where it is"
if [ $N = 1 ]; then
echo "    2. the harness shows the two unknown items and asks: type yes to write them off (nothing is written before that)"
echo "    3. one new SANDBOX top-up of the 12-credit pack (fake money): you click once in PayPal, nothing to type; the webhook is exported by a watcher"
else
echo "    2. the two unknown items were already written off by your typed yes in an earlier resume (audit records checked): nothing is written off again"
echo "    3. the sandbox top-up is already paid and the balance is positive: no purchase, no PayPal click"
fi
echo "    4. only then the generator continues from its saved tests checkpoint (tests T-1..T-12 are kept; T-13 onward runs)"
echo "  spend ceiling: USD $CEILING for the whole run. Already spent USD $SPENT plus USD $RESV reserved by the never-started call, so a balance watcher"
echo "  stops the container at a further drop of USD $REMAIN from the current DeepSeek balance USD $B (SIGINT first so the database is archived, hard kill after 90s)."
echo "  The database is archived to evidence/item6-database-resume-$N.dump (a new file); the original dump is never overwritten."
echo "  Acceptance stays INCOMPLETE: this is a resume, not an uninterrupted run."
echo "============================================================"
read -r -p "To start, type exactly:  GO $RID CEILING $CEILING   > " PHRASE
[ "$PHRASE" = "GO $RID CEILING $CEILING" ] || die NOT-STARTED "go phrase not typed exactly" "Nothing was started."
echo "== evidence backup on the server"
"${UBU[@]}" "NAME=$NAME N=$N bash -s" <<'BK' || die BACKUP "the evidence backup failed" "The original evidence is untouched; a partial copy may exist on the server (evidence-pre-resume-$N): do not run, tell me."
set -eu
cd ~/item6/runs/$NAME; umask 077
[ ! -e evidence-pre-resume-$N ]
(cd evidence && find . -type f | sort | while IFS= read -r f; do sha256sum "$f"; done) > manifest-before-resume-$N.txt
cp -a evidence evidence-pre-resume-$N
(cd evidence-pre-resume-$N && find . -type f | sort | while IFS= read -r f; do sha256sum "$f"; done) | cmp - manifest-before-resume-$N.txt
echo "ok  $(wc -l < manifest-before-resume-$N.txt) files copied to evidence-pre-resume-$N and hashes match"
BK
echo "== starting"
"${UBU[@]}" "NAME=$NAME RID=$RID CEILING=$REMAIN START=$B CONT=$CONT N=$N bash -s" <<'INNER'
set -eu
cd ~/item6/runs/$NAME; umask 077
cat > go-resume-inner.sh <<EOS
#!/usr/bin/env bash
cd "\$(dirname "\$0")"; umask 077
TS=\$(date +%Y%m%dT%H%M%S)
python3 watcher-resume.py "$CONT" "$CEILING" "$START" >> "resume-watch-\$TS.log" 2>&1 &
W=\$!
if [ $N = 1 ]; then bash webhook-watch-topup.sh "$RID" "$CONT" >> "resume-webhook-watch-\$TS.log" 2>&1 & W2=\$!; else W2=; fi
script -qefc "bash run-resume.sh --go" "resume-console-\$TS.log"
RC=\$?
kill \$W \$W2 2>/dev/null || true
echo; echo "== resume exited with code \$RC. Evidence: ~/item6/runs/$NAME/evidence. Press Enter to close this window."; read -r _
EOS
cat > watcher-resume.py <<'EOW'
import glob,json,subprocess,sys,time,urllib.request
name,ceiling,start=sys.argv[1],float(sys.argv[2]),float(sys.argv[3])
cred=glob.glob('private/runtime-*/credentials.json')[0]; key=json.load(open(cred))['deepseek_api_key']
def bal():
    r=urllib.request.Request('https://api.deepseek.com/user/balance',headers={'Authorization':'Bearer '+key})
    d=json.load(urllib.request.urlopen(r,timeout=20)); return float([b for b in d['balance_infos'] if b['currency']=='USD'][0]['total_balance'])
def log(m): print(time.strftime('%FT%T%z'),m,flush=True)
def state(): return subprocess.run(['sudo','-n','docker','inspect','-f','{{.State.Status}}',name],capture_output=True,text=True).stdout.strip()
fails=0; t0=time.time(); log('watcher start balance %.2f ceiling %.2f (further drop)'%(start,ceiling))
while True:
    st=state()
    if st in ('exited','dead'): log('container %s; watcher done'%st); break
    if st=='running':
        try:
            b=bal(); fails=0; spent=start-b; log('balance %.2f spent %.2f elapsed %ds'%(b,spent,time.time()-t0))
            if spent>=ceiling:
                log('CEILING REACHED: sending SIGINT'); subprocess.run(['sudo','-n','docker','kill','--signal=INT',name])
                for _ in range(18):
                    time.sleep(5)
                    if state()!='running': break
                else:
                    log('still running after 90s: hard kill'); subprocess.run(['sudo','-n','docker','kill',name])
                break
        except Exception as e:
            fails+=1; log('balance read failed (%s) %d'%(type(e).__name__,fails))
            if fails>=10: log('10 failed balance reads in a row: stopping the container (cannot enforce the ceiling)'); subprocess.run(['sudo','-n','docker','kill','--signal=INT',name]); break
    time.sleep(30)
EOW
cat > webhook-watch-topup.sh <<'EOV'
#!/usr/bin/env bash
# Exports the PayPal sandbox capture of THIS run's top-up (invoice id item6-topup-<run id>-01) into topup-webhook/ once it arrives.
# Exactly one match required; never overwrites; exits after one export or when the container is gone.
RID=$1; CONT=$2; umask 077; cd "$(dirname "$0")"
D=topup-webhook; done_=0
while [ $done_ = 0 ]; do
  st=$(sudo -n docker inspect -f '{{.State.Status}}' "$CONT" 2>/dev/null)
  case "$st" in exited|dead) echo "$(date +%FT%T%z) container $st; watcher done"; exit 0;; esac
  M=""; N=0
  for f in $(sudo -n find /var/lib/requi-receiver/spool -maxdepth 1 -type f -name '*.json' | sort); do
    r=$(sudo -n cat "$f" | RID="$RID" python3 -c '
import sys,json,base64,os
try:
    e=json.load(sys.stdin); b=json.loads(base64.b64decode(e["raw_body_b64"]))
    print("MATCH" if b.get("event_type")=="PAYMENT.CAPTURE.COMPLETED" and (b.get("resource") or {}).get("invoice_id")=="item6-topup-"+os.environ["RID"]+"-01" else "no")
except Exception: print("no")
')
    [ "$r" = MATCH ] && { N=$((N+1)); M=$(basename "$f" .json); }
  done
  echo "$(date +%FT%T%z) receiver spool scanned: $N matching top-up capture(s)"
  if [ "$N" = 1 ]; then
    F=webhook-$(date +%Y%m%d%H%M%S).json
    sudo -n -u requi-receiver python3 /opt/requi-receiver/receiver.py export --receipt "$M" > "$D/$F"
    if [ -s "$D/$F" ] && [ "$(stat -c %a "$D/$F")" = 600 ] && [ "$(stat -c %u "$D/$F")" = 1000 ]; then echo "$(date +%FT%T%z) exported capture $M to $F"; done_=1; else rm -f "$D/$F"; echo "$(date +%FT%T%z) export failed; removed"; fi
  elif [ "$N" -gt 1 ]; then echo "$(date +%FT%T%z) $N matching captures: refusing to choose"; exit 2
  fi
  sleep 10
done
EOV
chmod 700 go-resume-inner.sh webhook-watch-topup.sh; chmod 600 watcher-resume.py
INNER
"${UBU[@]}" -tt "tmux new-session -s $SESS 'bash ~/item6/runs/$NAME/go-resume-inner.sh'" || true
echo
echo "tmux window closed or detached. Reattach: ssh -t ubuntu@$HOST tmux attach -t $SESS"
echo "== collecting evidence"
OUTD=$DELIVERY/runs/$NAME-resume$N-collected-$(date +%Y%m%d%H%M%S)
mkdir -m 700 "$OUTD"
"${UBU[@]}" "cd $RDIR && tar cf - evidence manifest-before-resume-$N.txt \$(ls resume-*.log 2>/dev/null)" | tar xf - -C "$OUTD" || die COLLECT "copy failed" "Partial files may be in $OUTD; keep them." "bash $HERE/item6-run.sh --resume $RUN collect"
chmod -R go-rwx "$OUTD"; echo "collected into $OUTD"
