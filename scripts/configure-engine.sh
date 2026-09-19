#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REQUIRE_ROOT="$(cd "$ROOT/.." && pwd)"
winners=()
while IFS= read -r candidate; do
  [ -x "$candidate" ] || continue
  if "$candidate" -c 'import langchain_core' >/dev/null 2>&1; then winners+=("$candidate"); fi
done < <(find "$REQUIRE_ROOT" -maxdepth 3 -type f -path '*/bin/python*' | sort)
if [ "${#winners[@]}" -eq 1 ]; then
  printf '%s\n' "${winners[0]}" > "$ROOT/.engine-python"
  printf 'Saved interview engine Python: %s\n' "${winners[0]}"
  exit 0
fi
if [ "${#winners[@]}" -eq 0 ]; then
  echo 'Error: no Python environment under the require tree can import langchain_core.'
  echo "Likely cause: interview-engine dependencies are not installed in a local venv."
  echo "Next: install interview-engine in your preferred venv, then rerun scripts/configure-engine.sh."
  exit 1
fi
echo 'Multiple compatible engine environments found:'
printf '  %s\n' "${winners[@]}"
echo 'Next: choose one and run: INTERVIEW_ENGINE_PYTHON=/path/to/python scripts/run-ui.sh'
exit 2
