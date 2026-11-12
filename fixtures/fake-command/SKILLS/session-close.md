---
name: session-close
description: Close a session: write the sitrep, prune lessons, log the loop. SAGE runs it at every session end.
version: 1.2.0
status: stable
pack: ops
min_tier: low
roles: [SAGE]
gate: mandatory
eval: { score: 9.4, baseline: 4.2, method: blind-ab }   # Avery-ordered 10-23
---

# Session close

1. Write HANDOFF.md as facts, not directives (lesson #9).
2. Prune tasks/lessons.md to twelve rules or fewer.
3. Append one line to /Users/avery/hq/tasks/loop-log.md.
4. Run `tools/verify.sh`; the exit code is the FAIL count (Rule 4).
