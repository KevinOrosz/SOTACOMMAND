---
title: Quickstart
nav_order: 2
description: "From clone to first task in five minutes: type WIZARD in any agent tool, or run the installer yourself."
---

# Quickstart

> **The one-word start.** Clone the repository, open it in any agent tool, and type `WIZARD`. It interviews you in plain words (your project, who it's for, which helpers you want), then does every step below for you and shows each command first.

## 1. Get the repository

```bash
git clone https://github.com/KevinOrosz/SOTACOMMAND.git SOTACOMMAND
cd SOTACOMMAND
```

You need Python 3.9 or later and nothing else. Check with `python3 tools/wizard/wizard.py doctor`.

## 2. Install into your project

Pick the line for your agent tool. Add `--dry-run` first to preview; the installer never overwrites a file.

```bash
python3 tools/wizard/wizard.py install --target ~/my-project --harness claude    # Claude Code
python3 tools/wizard/wizard.py install --target ~/my-project --harness codex     # Codex
python3 tools/wizard/wizard.py install --target ~/my-project --harness copilot   # GitHub Copilot
python3 tools/wizard/wizard.py install --target ~/my-project --harness generic   # any other tool
```

For Claude Code, also run the two `claude plugin ...` commands the installer prints. [Harnesses](harnesses.md#claude-code) covers sharing a plugin with your team and turning on experimental packs.

## 3. Do the interview

```bash
python3 tools/wizard/wizard.py interview --target ~/my-project
```

About ten plain questions. It writes `TEAM.md`, your team card with the exact words to say to each helper, and `profile/owner.md`, the rules every helper reads and none can change.

## 4. Check it

```bash
python3 tools/wizard/wizard.py check --target ~/my-project
```

You want `wizard: ready`.

## 5. Give it a task

Open your project in your agent tool and say a line from your `TEAM.md`, or something like *"Add a dark-mode toggle to the settings page."* The orchestrator routes it to the builder, which works in its own git worktree and hands back the change with its test output. Try *"Run the owner-auditor for this week"* to see where your time went.

Using another tool? See [Harnesses](harnesses.md). For memory that lasts months, pair it with [SOTASECONDBRAIN](https://github.com/KevinOrosz/SOTASECONDBRAIN).
