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
# Prompts are complete terminal lines before input blocks. Never route them
# through a line-buffered stream filter or command substitution.
ops_read(){
  local destination=$1; shift
  local terminal=${PREVIEW_TTY:-/dev/tty} color=''
  [[ "$destination" =~ ^[a-zA-Z_][a-zA-Z0-9_]*$ ]] || return 2
  # A scoped redirection restores any caller FD 8 and works on macOS Bash 3.2.
  if {
    if ! [ -t 8 ]; then error 'Input requires a terminal; nothing confirmed.' >&2; return 2; fi
    [ -n "${NO_COLOR+x}" ] || color=36
    if [ -n "$color" ]; then printf '\033[%sm%s\033[0m\n' "$color" "$*" >&8
    else printf '%s\n' "$*" >&8; fi
    IFS= read -r "$destination" <&8
  } 8<>"$terminal"; then return 0
  else
    local result=$?
    error 'Input ended or no terminal was available; nothing confirmed.' >&2
    return "$result"
  fi
}
