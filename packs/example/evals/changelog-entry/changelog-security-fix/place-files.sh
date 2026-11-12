#!/usr/bin/env bash
# Seeds the eval workspace with this case's input files.
#
# claude plugin eval runs this script only under --scaffold, as
# `bash <case dir>/place-files.sh`, with the empty run workspace as the
# working directory. It copies everything in the case's files/ directory into
# that workspace. A case without files/ gets an empty workspace.
set -euo pipefail

case_dir="$(cd "$(dirname "$0")" && pwd)"
if [ -d "$case_dir/files" ]; then
  cp -R "$case_dir/files/." .
fi
