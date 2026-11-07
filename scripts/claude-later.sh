#!/usr/bin/env bash
# Run one Claude Code task later in a FRESH, small session (no old history = few tokens).
#   scripts/claude-later.sh "HH:MM" "task prompt"     # start at a local time (today/tomorrow)
#   scripts/claude-later.sh now "task prompt"
# Output: .dev/logs/claude-later-<stamp>.log. Detached (survives closing the terminal).
# Stop it: pkill -f claude-later-run   ·   Watch: tail -f .dev/logs/claude-later-*.log
set -euo pipefail
cd "$(dirname "$0")/.."
WHEN=${1:?time HH:MM or now}; PROMPT=${2:?prompt}
mkdir -p .dev/logs
LOG=".dev/logs/claude-later-$(date +%Y%m%d-%H%M%S).log"
if [[ "$WHEN" == now ]]; then DELAY=0; else
  target=$(date -d "$WHEN" +%s); now=$(date +%s)
  if (( target <= now )); then target=$(date -d "tomorrow $WHEN" +%s); fi
  DELAY=$(( target - now ))
fi
PFILE="${LOG%.log}.prompt"; printf '%s' "$PROMPT" >"$PFILE"
# shellcheck disable=SC2016
nohup setsid bash -c 'sleep "$1"; exec claude -p --permission-mode auto "$(cat "$2")"' \
  claude-later-run "$DELAY" "$PFILE" >"$LOG" 2>&1 </dev/null &
echo "scheduled in ${DELAY}s -> $LOG"
