---
name: herald-schedule
description: Schedule HERALD's weekly read of audience evidence and route the brief to RUNE.
version: 0.3.0
status: experimental
pack: audience
min_tier: low
roles: [HERALD]
gate: none
---

# Weekly audience read

Every Monday, run the herald-schedule loop: export last week's comments read-only,
write the brief and hand it to SAGE. This skill replaces herald-brief.
