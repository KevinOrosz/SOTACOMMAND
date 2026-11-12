---
name: loop-brief
description: "Turn one loop's log into a one-screen brief: target, batch, metric, verdict."
version: 1.0.0
status: stable
pack: ops
min_tier: mid
roles:
  - SAGE
  - RUNE
gate: none
allowed-tools: [Read, Grep, Glob]
eval:
  score: 8.8
  baseline: 5.1
  method: blind-ab
---

# Loop brief

Read the last run in tasks/loop-log.md and write five lines:

- Target, batch and metric.
- What changed since the last run.
- The verdict: keep, revert or plateau.
- The next batch, or "stop" with the reason.
- The owner decision, if any. Avery reads this line first.
