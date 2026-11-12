# sotacommand

A portable operating system for a team of AI agents: roles, skills, memory and
governance, shipped as plain files that any harness can read.

## Install

Claude Code 2.1.277 or later:

    claude plugin marketplace add owner/sotacommand
    claude plugin install sotacommand-engine@sotacommand

Codex reads `AGENTS.md` and `.agents/skills/` natively. GitHub Copilot picks up
`.github/agents/*.agent.md`. Cursor and Gemini CLI support arrive in v0.2.

Version 0.1.0 ships the engine plugin and one example pack. Questions go to
hello@example.com.

<!-- cta -->
