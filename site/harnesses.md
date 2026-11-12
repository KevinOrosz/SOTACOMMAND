---
title: Harnesses
nav_order: 4
description: "Set up Claude Code from the plugin marketplace; Codex, Cursor and Gemini CLI through AGENTS.md; GitHub Copilot through .github/agents; any chat assistant through one prompt file."
---

# Harnesses

The roles, skills and rules are written once, as plain files. Thin adapters give each harness the entry files it expects, so nothing is maintained twice.

| Harness | Entry point | Skills | Status |
|---|---|---|---|
| Claude Code | Plugins from this repository's marketplace | Inside each plugin, under `skills/` | v0.1: engine plugin and skill packs |
| Codex | `AGENTS.md` at the project root | `.agents/skills/` | v0.1 |
| GitHub Copilot | Custom agents in `.github/agents/` | Through the agents | v0.1, generated with the engine |
| Cursor | `AGENTS.md`, plus the always-applied rule `.cursor/rules/sotacommand.mdc` | `.agents/skills/` | v0.1 |
| Gemini CLI | `GEMINI.md`, which imports `AGENTS.md` | `.agents/skills/` | v0.1 |
| Hermes Agent | `AGENTS.md` at the project root | `.agents/skills/`, after `hermes skills trust` | v0.1, not yet run on a real Hermes install |
| Any chat assistant or wrapper | `SOTACOMMAND.prompt.md`, one file to paste into its instructions | Inlined in that file | v0.1 |

## Claude Code

### Install from the marketplace

Add this repository as a plugin marketplace, then install the plugins you want:

```bash
claude plugin marketplace add KevinOrosz/SOTACOMMAND
claude plugin install <plugin>@sotacommand
```

The marketplace is named `sotacommand`, and the engine plugin is `sotacommand-engine`; `<plugin>` below is any plugin it lists. To browse the plugins, run `/plugin` in a Claude Code session and open the **Discover** tab. Inside a session, `/plugin install <plugin>@sotacommand` does the same as the shell command, and lets you review the plugin and pick a scope first.

### Share a plugin with your team

```bash
claude plugin install <plugin>@sotacommand --scope project
```

Project scope records the plugin in your repository's `.claude/settings.json`, which you commit. That turns the plugin on for collaborators, but it doesn't download it for them, so each collaborator runs the same command once. Use `--scope local` to enable a plugin for yourself in one repository only.

### Turn on an experimental pack

Packs under `labs/` are unproven, so they install switched off. Turn one on when you want to try it:

```bash
claude plugin enable <plugin>@sotacommand
```

### Update

```bash
claude plugin marketplace update sotacommand
claude plugin update <plugin>@sotacommand
```

The first command refreshes the marketplace listing; the second updates one installed plugin. A running session keeps the version it loaded, so run `/reload-plugins` or start a new session to pick up the update.

### Project instructions

Claude Code v2.1.277 and later reads `AGENTS.md` as your project instructions when there's no `CLAUDE.md` in your working directory or above it. If you keep a `CLAUDE.md`, add a line with `@AGENTS.md` to it to import the same file. Claude Code never loads a `CLAUDE.md` that sits inside a plugin, so the OS ships its own instructions as skills.

### What the engine plugin adds

- Each role becomes a plugin agent whose tool list enforces the role's boundary.
- Sessions start as the orchestrator, through the plugin's `settings.json`.
- Hooks run the session ritual: a preflight when a session opens, a close gate when it ends, and a guard that blocks writes to protected paths.

## Codex

Codex reads `AGENTS.md` and the skills in `.agents/skills/`. From a clone of this repository, the installer writes both into your project:

```bash
git clone https://github.com/KevinOrosz/SOTACOMMAND.git
python3 SOTACOMMAND/tools/wizard/wizard.py install --target ~/my-project --harness codex
```

It copies each skill folder (copies, not symlinks, so it also works on Windows) and writes an `AGENTS.md` with the role table, but only if your project doesn't have one yet. Run it again after `git pull` to update.

## GitHub Copilot

Copilot reads custom agent profiles from `.github/agents/` in your repository. The engine generates one profile per role, `.github/agents/<role>.agent.md`, with the role's description and tool list, so the same boundaries apply in Copilot. Install them with `python3 tools/wizard/wizard.py install --target ~/my-project --harness copilot`. Copilot doesn't enforce write scopes or worktrees, so each profile states those limits as rules.

## Cursor

Cursor reads `AGENTS.md` at the project root. The installer also writes `.cursor/rules/sotacommand.mdc`, a project rule with `alwaysApply: true` that points to `AGENTS.md`, so the roles and the boot and close steps apply in every chat:

```bash
python3 tools/wizard/wizard.py install --target ~/my-project --harness cursor
```

## Gemini CLI

Gemini CLI reads `GEMINI.md`. The installer writes one that imports `AGENTS.md` with Gemini CLI's `@./AGENTS.md` syntax, so there is still only one file of instructions to edit:

```bash
python3 tools/wizard/wizard.py install --target ~/my-project --harness gemini
```

## Hermes Agent

Hermes Agent is a full agent runtime with its own tools, sandboxes and memory. SOTACOMMAND runs on top of it as the team and its rules. According to Hermes's documentation, it reads `AGENTS.md` from the project and loads project skills from `.agents/skills/` once you trust the project, so the neutral install is all it needs:

```bash
python3 tools/wizard/wizard.py install --target ~/my-project --harness hermes
hermes skills trust ~/my-project
```

Three things to know:

- Hermes finds project skills from the nearest git project, so the folder needs `git init`.
- Hermes loads one project instruction file, first match wins: `.hermes.md`, then `AGENTS.override.md`, then `AGENTS.md`. If one of the first two is in your folder, your team won't load. The installer and `wizard.py check` warn you.
- Hermes doesn't know the helpers' limits. They are rules the Lead follows; Hermes's own command approval and sandbox backends are the hard boundary. Your Hermes identity file (`SOUL.md` in its home folder) is never touched.

This adapter is built from Hermes's documentation and has not been run on a real Hermes install yet.

## Any other chat assistant or wrapper

For a tool that can't read project files (a chat UI, a custom GPT-style assistant, an agent wrapper with a single system prompt), install the flat prompt. `SOTACOMMAND.prompt.md` holds the roles, the boot and close steps and every installed skill in one file; paste it into the tool's instructions:

```bash
python3 tools/wizard/wizard.py install --target ~/my-project --harness flat
```

Without a file system there's no `memory/`, so the prompt asks the assistant to hand you the end-of-session sitrep to paste into the next chat. Nothing in it is enforced: treat every boundary as a rule the model follows, not one the tool guarantees.

## Update

After `git pull` in your SOTACOMMAND folder:

```bash
python3 tools/wizard/wizard.py update --target ~/my-project --dry-run
python3 tools/wizard/wizard.py update --target ~/my-project
```

A file the installer wrote and you haven't changed is replaced. One you edited is kept, and the new version is written beside it as `<name>.new`. Your team card is rebuilt from your interview answers. `os.config.json` only gains what it lacks (new settings and protected paths), after a backup to `os.config.json.bak`. `wizard.py check` tells you when a project is behind.

## Uninstall

Every file the installer writes is recorded, with its hash, in `.sotacommand/install.json`. To take SOTACOMMAND out of a project:

```bash
python3 tools/wizard/wizard.py uninstall --target ~/my-project --dry-run
python3 tools/wizard/wizard.py uninstall --target ~/my-project
```

It removes only recorded files that are unchanged since the install. A memory file or config you've edited is kept and listed, and a file the installer didn't write is never touched. For Claude Code, also run `claude plugin uninstall <plugin>@sotacommand`.
