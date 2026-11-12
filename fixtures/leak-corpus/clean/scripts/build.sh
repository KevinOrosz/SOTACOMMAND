#!/usr/bin/env bash
# Build the docs site. Stops cleanly on SIGINT or SIGTERM.
set -euo pipefail
trap 'echo "interrupted by signal" >&2; exit 130' INT TERM
cd "${HOME}/projects/sotacommand"
python3 tools/repo/site/build.py --check
echo "built version ${VERSION:-0.1.0}"
