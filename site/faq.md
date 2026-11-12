---
title: FAQ
nav_order: 6
description: "Answers on setup help, models, harnesses, the stable rule, re-running evals, network use, updates and contributing."
---

# FAQ

## Can someone set this up for me?

Yes. Setup help is available for teams that would rather have it installed and tuned for them. Nothing here depends on it, though: the [quickstart](quickstart.md) and the [harness guide](harnesses.md) cover installing everything yourself.

<!-- cta -->

## Do I need a particular model?

No. Roles declare the capability tier they need (low, mid or high) instead of naming a model. In Claude Code, the role agents run on whatever model your session uses. Eval runs are the one place a model is pinned, so that scores stay comparable over time; CI sets it through repository variables, and no model name is written in any file.

## Which harnesses does it support?

Claude Code, Codex, GitHub Copilot, Cursor, Gemini CLI and Hermes Agent, plus any chat assistant or agent wrapper through one flat prompt file. Only Claude Code enforces the role boundaries with tool limits, hooks and worktrees; elsewhere they are rules the model follows. [Harnesses](harnesses.md) has the setup for each.

## What does "stable" mean for a skill?

A stable skill has a with-skill score of at least 0.80 and a Δ of at least +0.20 against no skill, over 3 or more runs per arm. Experimental skills load but haven't proven themselves yet, so they're offered, never fired on their own. [Qualify a skill](qualify-a-skill.md) has the details.

## Why publish results for skills that don't beat no skill?

Because an honest negative result is useful. It tells you where not to rely on a skill, and it stops a weak skill from looking proven. A skill with a Δ at or below 0 gets reworked or retired, and the [eval dashboard](evals/index.html) marks it as a regression in the meantime.

## Can I re-run the evals myself?

Yes. You need Claude Code v2.1.269 or later and your own model access, because every run is a real model call on your account. The cases ship with each pack, and [Qualify a skill](qualify-a-skill.md) gives the exact command. Expect some variation between runs, and more if you use a different model.

## Do the repository's tools call the network or a model?

No. The tools are standard-library Python, and they make no network calls and no model calls: models plan and judge, tools check. Eval runs are the exception, since they exist to call a model.

## Will updates overwrite my settings and notes?

They're designed not to. The engine keeps your instance files, meaning your configuration, your profile and your memory, in your own project, where a plugin update never touches them. The installer (`tools/wizard/wizard.py install`) only ever adds files that don't exist yet, and the tests check that it never overwrites one.

## What can the owner-auditor see and change?

It reads what the system produces, and it writes only to memory. In the Claude Code engine, that limit is enforced by a protected-path hook rather than by instructions. It never takes on a health, finance, security or wellbeing lane, and it doesn't review specialist work.

## How do I propose a new skill?

Open a skill proposal in the repository's issues. A new skill starts as experimental in a pack under `labs/`, and it becomes stable after a qualifying run that meets the stable bar. `CONTRIBUTING.md` in the repository covers the details and the checks to run locally.

## Is there a version of these docs for agents?

Yes. [llms.txt](llms.txt) indexes every page with a one-line summary, and [llms-full.txt](llms-full.txt) holds every page in one file.
