#!/usr/bin/env bash
# recall.sh: one search verb across every memory tier.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
query="${1:?usage: recall.sh QUERY}"

for tier in HANDOFF.md tasks KB; do
  grep -rn --color=never -- "$query" "$ROOT/$tier" 2>/dev/null || true
done
