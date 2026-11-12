---
title: Overview
nav_order: 1
description: "What the project is: a portable operating system for a team of AI agents, shipped as plain files, with each skill's eval results published."
---

# Run your AI agents like a team

This project is a portable operating system for a team of AI agents. Every agent gets a role with hard boundaries. Skills have to prove they help before anyone relies on them. Memory stays small enough to load at the start of every session, and governance checks work before it's called done. Everything is plain files that any harness can read, and nothing in them is tied to one model vendor.

> **Status: pre-release.** The engine plugin, the skills toolchain, these docs and the eval dashboard are built and tested. Open gaps are listed in the repository's `ROADMAP.md`. To start, open the repository in any agent tool and type `WIZARD`.

## Four layers in one place

Each of these layers is easy to find on its own. A prompt pack gives you skills without governance. A framework gives you code without an operating doctrine. A memory system gives you recall without a team. This project ships all four, designed to work together:

| Layer | What you get |
|---|---|
| **Team** | Role contracts with hard boundaries and handoffs. Each role owns one lane and hands back a defined result. |
| **Skills** | A library with a status lifecycle: experimental, stable and retired. Every stable skill ships its eval cases and results. |
| **Memory** | Three tiers with size caps and decay, so the context loaded at the start of a session stays small and current. |
| **Governance** | Routing, pushback, convergence, an audit of the owner's time, and verify-before-done. |

[Concepts](concepts.md) explains each layer with a diagram.

## Proof, not claims

A skill is called stable only when it measurably beats having no skill at all. Each skill ships eval cases in the standard `claude plugin eval` format. Every case runs three times with the skill and three times without it, which gives two numbers: the with-skill score, from 0 to 1, and Δ, how far that score sits above the no-skill baseline.

- **Stable** means a with-skill score of at least 0.80 and a Δ of at least +0.20, over 3 or more runs.
- **Experimental** means the skill loads but hasn't proven itself yet.
- **Δ at or below 0** means the skill doesn't beat no skill, so it gets reworked or retired. That result is published too.

The [eval dashboard](evals/index.html) lists every measured skill, and [Qualify a skill](qualify-a-skill.md) shows how to re-run the numbers yourself.

## A role that audits your time

One role, the owner-auditor, measures what the agent team costs you: the hours you spend steering it, the interventions you have to repeat, and the one fix that would save you the most time. It reports facts and a single recommendation, and it leaves the decision with you. It has no lane in health, finance, security or wellbeing, and it doesn't review specialist work.

## Works where you work

| Harness | How it reads the OS |
|---|---|
| Claude Code | Plugins from this repository's marketplace |
| Codex | `AGENTS.md` and skills in `.agents/skills/` |
| GitHub Copilot | Custom agents in `.github/agents/` |
| Cursor | `AGENTS.md` and an always-applied rule in `.cursor/rules/` |
| Gemini CLI | `GEMINI.md`, which imports `AGENTS.md` |
| Hermes Agent | `AGENTS.md`, plus the skills in `.agents/skills/` once the project is trusted |
| Any chat assistant | One flat prompt file, `SOTACOMMAND.prompt.md` |

Setup for each is on [Harnesses](harnesses.md).

## Start here

- [Quickstart](quickstart.md): install what exists today.
- [Concepts](concepts.md): team, skills, memory and governance.
- [Qualify a skill](qualify-a-skill.md): the stable rule, and how to run the evals with a no-skill baseline.
- [FAQ](faq.md): models, harnesses, updates and setup help.
