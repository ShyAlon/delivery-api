# Severity belongs to the emitting call, never to words in the message.
# No stream filter, subprocess, or ANSI escapes in redirected/saved output.
ops_log(){
  local severity=$1; shift
  local color=''
  if [ -t 1 ] && [ -z "${NO_COLOR+x}" ]; then
    case "$severity" in error) color=31;; warning) color=33;; prompt) color=36;; esac
  fi
  if [ -n "$color" ]; then printf '\033[%sm%s\033[0m\n' "$color" "$*"
  else printf '%s\n' "$*"; fi
}
error(){ ops_log error "$@"; }
warn(){ ops_log warning "$@"; }
prompt(){ ops_log prompt "$@"; }
