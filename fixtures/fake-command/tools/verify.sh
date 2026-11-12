#!/usr/bin/env bash
# verify.sh: deterministic state check. The exit code is the FAIL count.
set -uo pipefail

ROOT="${OS_ROOT:-/home/avery/hq}"
fails=0

check_cap() {
  local file="$1" cap="$2" lines
  lines=$(wc -l < "$ROOT/$file" 2>/dev/null || echo 0)
  if [ "$lines" -gt "$cap" ]; then
    echo "FAIL $file has $lines lines (cap $cap)"
    fails=$((fails + 1))
  fi
}

check_cap HANDOFF.md 15
check_cap tasks/lessons.md 12
exit "$fails"
