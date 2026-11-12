---
name: os-boot
description: Boot protocol for the SOTACOMMAND engine. Use at the start of a session, after /clear or compaction, or when the owner names a registered role, to restore state from T1 memory, report preflight facts and route work to the role that owns it.
license: Apache-2.0
metadata:
  version: "0.1.0"
  status: experimental
  pack: engine
  min-tier: low
  roles: {{MAIN_ROLE}}
  gate: mandatory
---

# Boot protocol

This session runs as the `{{MAIN_ROLE}}` role: the plugin's `settings.json` makes it the main agent. The owner can choose another agent with `--agent` or their own `agent` setting.

## On boot

1. If a `session-open:` line says `BOOT_INVALID`, stop. Tell the owner in one line that the engine's role folder, registry and router disagree, quote the `BOOT_INVALID` lines, and route no work until the engine is regenerated or reinstalled. Guessing which copy is right is how roles drift.
2. Read the `session-open:` lines that the SessionStart hook added to your context. They are facts about the memory files and their caps. Report OVER, MISSING and INVALID lines as facts, never as instructions.
3. Read `profile/owner.md` and `TEAM.md` if they exist. The first says who the owner is, how to sound, what always needs their OK and what never to do: follow it in everything you write or decide. The second lists which helpers are on: never hand work to a helper it lists as off. If neither exists, say in one line that the owner hasn't done the interview yet, and that typing `WIZARD` in their SOTACOMMAND folder starts it.
4. Read `memory/HANDOFF.md` and `memory/lessons.md` if they exist. They are the only memory read at boot (T1). Read other memory files only when a task needs them.
5. Reply in at most five lines: the state you restored, the headline of the board, and whether you are waiting for the owner or acting on a task already given. The owner steers, so do not open with directives.

If the project has no `os.config.json`, say so in one line and point to the template at `${CLAUDE_PLUGIN_ROOT}/templates/os.config.json`. Copying it into the project is the owner's decision.

## Routing

One role owns each artifact. Route each task to the role whose lane covers it. Keep work in your own lane only when it is intake, routing, sequencing, synthesis or acceptance. These boundaries are enforced by tool limits, the protected-path hook and worktrees, not by instructions:

{{ROLE_TABLE}}

Delegate with the Agent tool and name the role by its plugin-scoped type, for example `{{PLUGIN_NAME}}:{{EXAMPLE_ROLE}}`. When a tool policy or the protected-path hook refuses an action, the boundary is working as designed: route the task to the role that owns it instead of retrying.

For a decision that deserves independent checks, run the `/{{PLUGIN_NAME}}:convergence` workflow. Its passes cannot see each other, and its synthesizer lists where they converge and where they contradict.

## Remembering earlier sessions

`memory/HANDOFF.md` only holds the latest note. Every earlier one is kept in `.sotacommand/history/`, one file per session, and you can't edit them. When the owner asks what was decided or done before ("what did we say about pricing?"), search there with Grep before you answer, and say which day's note you found it in. A helper that can run commands gets ranked results from `python3 "${CLAUDE_PLUGIN_ROOT}/tools/recall.py" "pricing decision"`. If nothing matches, say so; never fill the gap from guesswork.

## Skills the team proposes

The owner-auditor may propose a skill in `memory/skill-proposals/<name>.md` when the same problem keeps coming back. Show the owner the proposal and ask. Only on their yes ("approve skill <name>"), hand this command to a helper that can run commands and works in the project folder itself (the researcher if it is on; otherwise the builder, adding `--root` with the project folder, because the builder works in its own copy): `python3 "${CLAUDE_PLUGIN_ROOT}/tools/skill_loop.py" draft <name>`. It writes the skill as experimental and records how often the problem happened before.

A `session-open: skill ...` line reports each such skill, measured from the ledger: `measuring`, `not helping yet`, `qualifies` or `unmeasured`. When one `qualifies`, tell the owner, and on their yes have the same helper run `... skill_loop.py promote <name>`. The tool refuses unless the ledger shows the problem stopped, so never mark a skill stable by editing its file.

## Close

When the owner says `close` (or "that's it for today", or anything that ends the day): rewrite `memory/HANDOFF.md` now, as described below, tell them in two lines what you saved and what comes first next time, and stop.

The Stop hook runs the close gate. When `close_gate.verify` is set in `os.config.json`, you cannot finish while it fails: fix the failure, or report it as a blocker together with the gate's output. Before the session ends, rewrite `memory/HANDOFF.md` as a sitrep of at most 15 lines (facts, not directives), and add any new standing lesson to `memory/lessons.md`.

## Reference

- [references/os-config.md](references/os-config.md): every `os.config.json` key the engine reads.
