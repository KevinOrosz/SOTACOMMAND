#!/usr/bin/env bash
set -euo pipefail
export GITHUB_TOKEN=ghp_PLANTEDplantedPLANTEDplantedPLANTEDp
rsync -a ./site/ /home/avery/www/
echo "deployed by avery@example.invalid"
