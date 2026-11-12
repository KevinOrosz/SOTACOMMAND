#!/usr/bin/env bash
# Seeds the eval workspace with this case's input files.
#
# claude plugin eval runs this script only under --scaffold, as
# `bash <case dir>/place-files.sh`, with the empty run workspace as the
# working directory. It copies everything in the case's files/ directory into
# that workspace, then drops the .fixture part of each name: the repository's
# leak gate refuses any committed file named HANDOFF.md, so the seeded
# handoff is stored as HANDOFF.fixture.md and renamed here.
set -euo pipefail

case_dir="$(cd "$(dirname "$0")" && pwd)"
if [ -d "$case_dir/files" ]; then
  cp -R "$case_dir/files/." .
fi
find . -name '*.fixture.md' -print0 | while IFS= read -r -d '' file; do
  mv "$file" "${file%.fixture.md}.md"
done
