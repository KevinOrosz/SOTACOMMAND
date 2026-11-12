# AGENTS.md

Instructions for AI agents working in this project. The SOTACOMMAND installer wrote this file because none existed. Edit it freely: the installer never overwrites an existing `AGENTS.md`.

## If the owner types WIZARD

Check the setup and show what the team can do:

1. Confirm `os.config.json`, `memory/HANDOFF.md` and `memory/lessons.md` exist. Report any that are missing; the owner can create them by running `python3 tools/wizard/wizard.py install --target <this project>` from their SOTACOMMAND clone.
2. Report the size of each memory file against the caps in `os.config.json` (`memory_caps`), as facts.
3. If `TEAM.md` exists, show its helper table: it has the owner's own helpers and the exact words to say. If it doesn't, tell the owner the interview hasn't been done, and that they can run `python3 tools/wizard/wizard.py interview --target <this project>` from their SOTACOMMAND clone (about ten plain questions).
4. List the roles below in one line each, then offer one example to try: *"Add a dark-mode toggle to the settings page"* goes to the builder; *"Run the owner-auditor for this week"* writes `memory/owner-ledger.md`.

## Roles

Work goes to functional roles. One role owns each artifact. The `{{MAIN_ROLE}}` role takes intake from the owner, routes each task to the role that owns it, sequences the work and accepts the results. The owner steers.

{{ROLE_TABLE}}

This harness does not enforce per-role tool limits, write scopes or worktrees. Treat every boundary above as a hard rule. The Claude Code plugin (`{{PLUGIN_NAME}}`) enforces the same boundaries with tool limits, hooks and worktrees.

## Boot

1. Read `profile/owner.md` and `TEAM.md` if they exist. The first says who the owner is, how to sound, what always needs their OK and what never to do: follow it in everything you write or decide. The second lists which helpers are on: never hand work to a helper it lists as off. If neither exists, say in one line that the owner hasn't done the interview yet, and that typing `WIZARD` in their SOTACOMMAND folder starts it.
2. Read `memory/HANDOFF.md` and `memory/lessons.md` if they exist. They are the only memory read at boot.
3. Report what you find as facts, not directives, in at most five lines. Greet the owner by name.
4. Wait for the owner unless a task is already given.

## Earlier sessions

If `.sotacommand/history/` exists, it holds past handoff notes, one file per session. Search it before you answer a question about what was decided or done before, and never edit it.

## Skills

Skills live in `.agents/skills/`. Installed by SOTACOMMAND:

{{SKILL_LIST}}

## Close

When the owner says `close`, or before you report work as done, run the project's verify command (`close_gate.verify` in `os.config.json`, when set) and report its result. Rewrite `memory/HANDOFF.md` as a sitrep of at most 15 lines.
