---
name: wizard
description: Setup and health check for SOTACOMMAND in this project. Use when the owner types WIZARD (any case), asks to set up or check the agent team, or asks how to use SOTACOMMAND here.
license: Apache-2.0
metadata:
  version: "0.1.0"
  status: experimental
  pack: engine
  min-tier: low
  roles: orchestrator
  gate: none
---

# WIZARD

The owner typed `WIZARD`. Set the project up if it isn't, check it, and show what the team can do. Say each step in one line before you do it. The owner decides; you never overwrite their files.

## 1. Is the project set up?

Look for `os.config.json`, `memory/HANDOFF.md` and `memory/lessons.md` in the project root.

If any is missing, say which, and ask: *"Copy the starter files into this project? (yes / no)"* On yes, copy only the missing ones:

- `${CLAUDE_PLUGIN_ROOT}/templates/os.config.json` → `os.config.json`
- `${CLAUDE_PLUGIN_ROOT}/templates/memory/HANDOFF.template.md` → `memory/HANDOFF.md`
- `${CLAUDE_PLUGIN_ROOT}/templates/memory/lessons.template.md` → `memory/lessons.md`

`os.config.json` is protected once it exists: the owner edits it by hand.

## 2. Check it

You can't run commands in this role, and you don't need to. The check already ran when this session started: its result is the `session-open:` lines in your context. Read them back to the owner. If there are none, read `os.config.json`, `memory/HANDOFF.md` and `memory/lessons.md` yourself and say which exist.

Report each line as a fact. `OK` is good. `OVER` means a memory file is longer than its cap. `MISSING` means step 1 isn't done.

## 3. Show the team

If `TEAM.md` exists, show its helper table first: it is the owner's own team card, with the helpers they chose and the exact words to say. If `profile/owner.md` or `TEAM.md` is missing, say: *"You haven't done the interview yet. It's about ten plain questions about you and your work. Open your SOTACOMMAND folder in your AI tool and type WIZARD, or run `python3 tools/wizard/wizard.py interview --target <this folder>` there."* You can't write `profile/` yourself: it is protected.

Then tell the owner, in plain words: this session is the **orchestrator**. It routes every task to one specialist: **builder** (code, in its own git worktree), **researcher** (evidence and specs), **owner-auditor** (what the team costs them in time), **sensor** (read-only audience evidence), **repurposer** (drafts only, never posts). Then offer one example to try now:

- *"Add a dark-mode toggle to the settings page."* goes to the builder.
- *"Run the owner-auditor for this week."* writes `memory/owner-ledger.md`.
- *"Run convergence on: should we change billing providers?"* runs `/sotacommand-engine:convergence`.

## 4. Hand over

Say: every session starts from `memory/HANDOFF.md` and ends by rewriting it; settings live in `os.config.json`; `WIZARD` brings this check back anytime.
