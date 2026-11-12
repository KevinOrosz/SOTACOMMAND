---
name: voice-audit
description: Audit a draft against Avery's voice markers before anything ships. Use it on every public draft.
version: 2.0.1
status: stable
pack: quality
min_tier: mid
roles: [BEACON, EMBER]
gate: mandatory
license: Apache-2.0
---

# Voice audit

1. KILL: strike every phrase on the kill list in the voice profile.
2. DISCIPLINE: one idea per sentence; cut hedges.
3. PROTECT: keep the markers that make the voice recognizable.
4. Judge with Claude Sonnet 4.5 at mid tier, then re-read the result yourself.
