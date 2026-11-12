#!/usr/bin/env bash
# session.sh: open and close preflight for the agent OS. Reports DRIFT and FAIL lines as facts.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LOG="/Users/avery/hq/tasks/loop-log.md"
DEFAULT_MODEL="claude-opus-4-1"

open_session() {
  echo "OPEN $(date +%F)"
  for file in HANDOFF.md tasks/lessons.md; do
    [ -f "$ROOT/$file" ] || echo "FAIL missing $file"
  done
  echo "model: $DEFAULT_MODEL"
}

close_session() {
  "$ROOT/tools/verify.sh" || echo "FAIL verify returned $?"
  echo "$(date +%F) close" >> "$LOG"
}

case "${1:-}" in
  open) open_session ;;
  close) close_session ;;
  *) echo "usage: session.sh open|close" >&2; exit 2 ;;
esac
