#!/bin/sh
# Release helper
SLACK_WEBHOOK="https://hooks.slack.com/services/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nted"
NPM_TOKEN=npm_PLANTEDplantedPLANTEDplantedPLANTEDp
curl -X POST "$SLACK_WEBHOOK" -d '{"text":"release by Avery Example"}'
